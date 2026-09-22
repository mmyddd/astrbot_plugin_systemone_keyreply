import sys
import time
import asyncio
import re
import shutil
from pathlib import Path
from typing import AsyncGenerator, Any

plugin_dir = str(Path(__file__).parent.resolve())
if plugin_dir not in sys.path:
    sys.path.insert(0, plugin_dir)

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig
from astrbot.api import logger
from astrbot.api.message_components import Plain, Image

try:
    from .qa_store import QAStore, SCOPE_GLOBAL, SCOPE_GROUP, SCOPE_PRIVATE, regroup_answers
    from .qa_engine import (
        MODE_CLASSIC,
        MODE_JEV,
        MODE_LABELS,
        resolve_mode,
        answer_text_of,
        answer_images_of,
        resolved_text,
        resolved_images,
        question_of,
        describe_match,
    )
    from .systemone_client import SystemOneClientWrapper
    from .classifier import MessageClassifier, REPLY_TYPE_NAMES
    from .reply_engine import ReplyEngine
    from .context_manager import ContextManager
    from .filters import MessageFilter
    from .utils import (
        CooldownTracker,
        calculate_reply_delay,
        normalize_failure_mode,
        normalize_confidence_level,
        normalize_model_mode,
        normalize_reply_style,
        normalize_reply_length_mode,
        normalize_filter_mode,
        normalize_systemone_base_url,
        normalize_systemone_model,
    )
except (ImportError, ValueError):
    from qa_store import QAStore, SCOPE_GLOBAL, SCOPE_GROUP, SCOPE_PRIVATE, regroup_answers
    from qa_engine import (
        MODE_CLASSIC,
        MODE_JEV,
        MODE_LABELS,
        resolve_mode,
        answer_text_of,
        answer_images_of,
        resolved_text,
        resolved_images,
        question_of,
        describe_match,
    )
    from systemone_client import SystemOneClientWrapper
    from classifier import MessageClassifier, REPLY_TYPE_NAMES
    from reply_engine import ReplyEngine
    from context_manager import ContextManager
    from filters import MessageFilter
    from utils import (
        CooldownTracker,
        calculate_reply_delay,
        normalize_failure_mode,
        normalize_confidence_level,
        normalize_model_mode,
        normalize_reply_style,
        normalize_reply_length_mode,
        normalize_filter_mode,
        normalize_systemone_base_url,
        normalize_systemone_model,
    )



CQ_IMAGE_REGEX = re.compile(r"\[CQ:image,[^\]]*?(?:url|file)=([^,\]]+)", re.IGNORECASE)


# ═══════════════════════════════════════════════════════════════
# WebUI 配置中心：可编辑字段白名单与取值归一化
# 页面与 _conf_schema.json 共用同一份 AstrBot 配置，字段名与默认值保持一致。
# ═══════════════════════════════════════════════════════════════

SECRET_MASK = "********"
SECRET_FIELDS = ("systemone_api_key",)

_EDITABLE_LIST_FIELDS = (
    "session_whitelist",
    "session_blacklist",
    "user_whitelist",
    "user_blacklist",
)

_EDITABLE_TEXT_FIELDS = (
    "qa_min_confidence",
    "systemone_api_key",
    "systemone_base_url",
    "systemone_model",
    "systemone_custom_model",
    "failure_mode",
    "model_mode",
    "custom_provider_id",
    "reply_length_mode",
    "reply_style",
    "custom_prompt",
    "reply_delay_mode",
    "filter_mode",
)

_EDITABLE_BOOL_FIELDS = (
    "enable_plugin",
    "enable_group",
    "enable_private",
    "enable_reply_delay",
    "ignore_commands",
    "ignore_bots",
    "ignore_pure_media",
    "enable_cache",
    "debug_log",
)

_EDITABLE_INT_FIELDS = (
    "systemone_timeout",
    "max_chars",
    "reply_delay_min",
    "reply_delay_max",
    "reply_delay_fixed",
    "context_message_count",
    "session_cooldown",
    "user_cooldown",
    "max_continuous_replies",
    "min_message_length",
    "max_message_length",
    "rate_limit_per_minute",
    "cache_ttl",
)

# 插件名：@register、数据目录、Web API 路由与热重载共用同一来源
PLUGIN_NAME = "astrbot_plugin_systemone_keyreply"

# 改名前的插件名（v1.0.2 及更早）：仅用于一次性数据目录迁移
LEGACY_PLUGIN_NAME = "astrbot_plugin_typesafe_keyreply"

# 改名前的配置键 → 现配置键。老用户升级后无需重填任何一项：
# 读取配置前先就地搬迁，旧键保留不删，避免动到 AstrBot 自身的配置增删逻辑。
LEGACY_CONFIG_KEY_MAP = {
    "typesafe_api_key": "systemone_api_key",
    "typesafe_base_url": "systemone_base_url",
    "typesafe_timeout": "systemone_timeout",
    "typesafe_model": "systemone_model",
    "typesafe_custom_model": "systemone_custom_model",
}


def _as_list(raw: object) -> list:
    """把任意输入归一化为列表（兼容换行/逗号分隔的字符串）。"""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        return list(raw)
    if isinstance(raw, str):
        text = raw.replace("\r\n", "\n").replace(",", "\n").replace("，", "\n")
        return [line.strip() for line in text.split("\n") if line.strip()]
    return [raw]


def _as_text_list(raw: object) -> list:
    """去空白、去重且保持原顺序的字符串列表。"""
    seen: set[str] = set()
    result: list[str] = []
    for item in _as_list(raw):
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _as_int(raw: object, default: int) -> int:
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return default


def _as_float(raw: object, default: float) -> float:
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _as_bool(raw: object) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    return str(raw).strip().lower() in ("1", "true", "yes", "on", "是", "开启", "启用")


def _mask_secret(value: object) -> str:
    """API Key 掩码：保留前 4 位与后 4 位，中间用掩码替换。"""
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return SECRET_MASK
    return f"{text[:4]}{SECRET_MASK}{text[-4:]}"


# 插件版本：@register 与状态 API 共用同一来源，避免两处不一致
PLUGIN_VERSION = "1.0.3"


@register(
    PLUGIN_NAME,
    "mmyddd",
    "固定问答表驱动的自动关键词回复插件。消息先经本地正则召回，命中后由 TypeSafe AI 的 SystemOne 判定是否真提问，再回复标准答案。",
    PLUGIN_VERSION,
    "https://github.com/mmyddd/astrbot_plugin_systemone_keyreply",
)
class SystemOneKeyReplyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config or {}

        # 0. 旧版配置键一次性迁移（typesafe_* → systemone_*），必须在读取配置之前
        self._migrate_legacy_config()

        # 1. 基础配置
        self.enable_plugin = self.config.get("enable_plugin", True)
        self.enable_group = self.config.get("enable_group", True)
        self.enable_private = self.config.get("enable_private", False)

        # 2. SystemOne 判定配置 (走 TypeSafe AI，支持中英文双语选项自动归一化与模型选择)
        self.systemone_api_key = self.config.get("systemone_api_key", "")
        # Base URL 只填根地址，SDK 会自动拼接 /v1/systemone
        self.systemone_base_url = normalize_systemone_base_url(
            self.config.get("systemone_base_url", "")
        )
        self.systemone_timeout = self.config.get("systemone_timeout", 10)
        self.systemone_model_raw = self.config.get(
            "systemone_model", "jev-latest (推荐最新旗舰)"
        )
        self.systemone_custom_model = self.config.get("systemone_custom_model", "")
        self.systemone_model = normalize_systemone_model(
            self.systemone_model_raw, self.systemone_custom_model
        )

        self.failure_mode = normalize_failure_mode(
            self.config.get("failure_mode", "静默，不回复")
        )
        # 3. 回复模型与生成配置
        self.model_mode = normalize_model_mode(
            self.config.get("model_mode", "跟随当前会话模型")
        )
        self.custom_provider_id = self.config.get("custom_provider_id", "")
        self.reply_length_mode = normalize_reply_length_mode(
            self.config.get("reply_length_mode", "简短 (50~100字)")
        )
        self.max_chars = self.config.get("max_chars", 200)
        self.reply_style = normalize_reply_style(
            self.config.get("reply_style", "自然")
        )
        self.custom_prompt = self.config.get("custom_prompt", "")

        # 4. 回复延时配置
        self.enable_reply_delay = self.config.get("enable_reply_delay", True)
        self.reply_delay_mode = self.config.get("reply_delay_mode", "随机延时 (推荐)")
        self.reply_delay_min = self.config.get("reply_delay_min", 1)
        self.reply_delay_max = self.config.get("reply_delay_max", 3)
        self.reply_delay_fixed = self.config.get("reply_delay_fixed", 2)

        # 5. 智能判断与上下文
        self.context_message_count = self.config.get("context_message_count", 3)

        # 6. 防刷屏与频控
        self.session_cooldown = self.config.get("session_cooldown", 30)
        self.user_cooldown = self.config.get("user_cooldown", 30)
        self.max_continuous_replies = self.config.get("max_continuous_replies", 2)

        # 7. 黑白名单配置
        self.filter_mode = normalize_filter_mode(
            self.config.get("filter_mode", "仅黑名单")
        )
        self.session_whitelist = self.config.get("session_whitelist", [])
        self.session_blacklist = self.config.get("session_blacklist", [])
        self.user_whitelist = self.config.get("user_whitelist", [])
        self.user_blacklist = self.config.get("user_blacklist", [])
        self.session_whitelist_set = {
            str(x).strip() for x in self.session_whitelist if str(x).strip()
        }
        self.session_blacklist_set = {
            str(x).strip() for x in self.session_blacklist if str(x).strip()
        }
        self.user_whitelist_set = {
            str(x).strip() for x in self.user_whitelist if str(x).strip()
        }
        self.user_blacklist_set = {
            str(x).strip() for x in self.user_blacklist if str(x).strip()
        }

        # 8. 消息卫生过滤（仅长度；关键词/正则过滤已随 QA 表统一而移除）
        self.min_message_length = self.config.get("min_message_length", 2)
        self.max_message_length = self.config.get("max_message_length", 2000)
        self.ignore_commands = self.config.get("ignore_commands", True)
        self.ignore_bots = self.config.get("ignore_bots", True)
        self.ignore_pure_media = self.config.get("ignore_pure_media", True)

        # 9. API 限流、缓存与调试
        self.rate_limit_per_minute = self.config.get("rate_limit_per_minute", 60)
        self.enable_cache = self.config.get("enable_cache", False)
        self.cache_ttl = self.config.get("cache_ttl", 60)
        self.debug_log = self.config.get("debug_log", False)

        # 初始化子模块
        self.systemone_client = SystemOneClientWrapper(
            api_key=self.systemone_api_key,
            base_url=self.systemone_base_url,
            timeout=self.systemone_timeout,
            rate_limit_per_minute=self.rate_limit_per_minute,
            enable_cache=self.enable_cache,
            cache_ttl=self.cache_ttl,
            failure_mode=self.failure_mode,
            model=self.systemone_model,
        )
        self.classifier = MessageClassifier(self.systemone_client)
        self.reply_engine = ReplyEngine(self.context)
        self.context_manager = ContextManager(max_history_per_session=20)
        self.cooldown_tracker = CooldownTracker()
        self.filter = MessageFilter()

        # 10. 固定问答表（KeyReply 兼容）
        # 固定问答表是唯一回复来源：未命中 Q 一律静默
        self.qa_min_confidence = normalize_confidence_level(
            self.config.get("qa_min_confidence", "中")
        )
        # 相关性判定是否启用改为「按问答条目」决定，模式本身固定为 jev 语义
        self.qa_mode = MODE_JEV
        self.qa_store = QAStore(self._plugin_data_dir())

        # 11. WebUI 配置中心（Dashboard 插件页）后端路由
        self._register_web_apis()

        logger.info(
            f"[SystemOne] 插件已加载. 启用状态: {self.enable_plugin}, "
            f"API配置: {self.systemone_client.is_configured()}, 模型: {self.systemone_model}, "
            f"API地址: {self.systemone_client.effective_base_url}, "
            f"群聊: {self.enable_group}, 私聊: {self.enable_private}"
        )

    async def _apply_reply_delay(self):
        """执行模拟真人思考与打字的延时"""
        delay = calculate_reply_delay(
            enabled=self.enable_reply_delay,
            mode=self.reply_delay_mode,
            min_delay=self.reply_delay_min,
            max_delay=self.reply_delay_max,
            fixed_delay=self.reply_delay_fixed,
        )
        if delay > 0:
            logger.info(f"[SystemOne] 模拟思考打字延时 {delay:.2f} 秒...")
            await asyncio.sleep(delay)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """监听群聊消息"""
        async for result in self._handle_message_event(event):
            yield result

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def on_private_message(self, event: AstrMessageEvent):
        """监听私聊消息"""
        async for result in self._handle_message_event(event):
            yield result

    async def _handle_message_event(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """
        核心 28 步决策流水线，每步均有清晰状态日志输出
        """
        # 1. 插件是否启用？
        if not self.enable_plugin:
            return

        # 2. 群聊 / 私聊启用检查（极速短路，未启用的会话类型在 0.001ms 内直接退出，零资源浪费）
        is_private = event.is_private_chat()
        if is_private and not self.enable_private:
            return
        if not is_private and not self.enable_group:
            return

        # 3. 是否机器人自己的消息？
        sender_id = str(event.get_sender_id() or "")
        self_id = str(event.get_self_id() or "")
        is_self = bool(self_id and sender_id == self_id)
        if is_self:
            return

        text = event.get_message_str()
        if text is None:
            text = ""
        text = text.strip()

        # 提取图片组件与链接
        image_urls = []
        has_image = False
        try:
            messages = event.get_messages() or []
            for comp in messages:
                comp_type = getattr(comp, "type", "") or comp.__class__.__name__
                if str(comp_type).lower() == "image" or comp.__class__.__name__ == "Image":
                    has_image = True
                    url = (
                        getattr(comp, "url", None)
                        or getattr(comp, "file", None)
                        or getattr(comp, "path", None)
                    )
                    if url:
                        image_urls.append(str(url).strip())
        except Exception as e:
            logger.debug(f"[SystemOne] 提取消息组件图片失败: {e}")

        # 如果组件未提取到但消息文本中含 CQ:image 码或 [图片]
        if not image_urls and "[CQ:image" in text:
            has_image = True
            for m in CQ_IMAGE_REGEX.finditer(text):
                image_urls.append(m.group(1).strip())
        elif "[图片]" in text:
            has_image = True

        # 若包含图片但文本为空，赋予占位标识
        if has_image and not text:
            text = "[图片]"

        sender_name = event.get_sender_name() or "群友"
        is_group = not is_private
        group_id = str(event.get_group_id() or "")
        session_id = str(event.get_session_id() or group_id or "")
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        session_candidates = [s for s in (group_id, session_id, umo) if s]

        img_suffix = (
            f" [附带{len(image_urls)}张图片]"
            if image_urls
            else (" [附带图片]" if has_image else "")
        )
        clean_text = text.replace("\r", " ").replace("\n", " ").strip()
        display_msg = ((clean_text[:30] + "...") if len(clean_text) > 30 else clean_text) + img_suffix
        is_cmd = self.filter.is_command_message(text)

        # 记录消息到上下文历史管理器
        self.context_manager.add_message(
            session_id=session_id,
            sender_id=sender_id,
            sender_name=sender_name,
            content=text,
            is_bot=is_self,
            is_command=is_cmd,
        )

        # 记录用户发言用于连续回复防刷判定（连续普通消息或冷却超期后才重置）
        self.cooldown_tracker.record_user_message(
            session_id, is_bot=is_self, session_cooldown=self.session_cooldown
        )

        # 4. 是否系统消息或命令消息？
        if is_cmd and self.ignore_commands:
            if self.debug_log:
                logger.debug(f"[SystemOne] [规则过滤] 忽略指令消息: \"{display_msg}\"")
            return

        # 5. 是否纯媒体/图片/表情空文本消息？
        is_pure_media = self.filter.is_pure_media_message(text)
        if self.ignore_pure_media and (
            is_pure_media or (has_image and (not text or text == "[图片]"))
        ):
            if self.debug_log:
                logger.debug(f"[SystemOne] [规则过滤] 忽略纯媒体/表情消息: \"{display_msg}\"")
            return

        # 日志记录进入决策流水线的消息
        logger.info(
            f"[SystemOne] [1/4 收到消息] 会话: {session_id} | 发送者: {sender_name}({sender_id}) | 内容: \"{display_msg}\""
        )

        # 6. 黑白名单检查 (黑名单 > 白名单，兼容多格式ID，直接使用预计算 set 提升检索效率)
        allowed, wl_reason = self.filter.check_whitelist_blacklist(
            session_id=session_candidates,
            user_id=sender_id,
            filter_mode=self.filter_mode,
            session_whitelist=self.session_whitelist_set,
            session_blacklist=self.session_blacklist_set,
            user_whitelist=self.user_whitelist_set,
            user_blacklist=self.user_blacklist_set,
        )
        if not allowed:
            logger.info(f"[SystemOne] [规则过滤] 黑白名单拦截 ({wl_reason})")
            return

        # 7. 消息长度检查（当包含图片且未开启忽略纯媒体时，跳过最短长度限制）
        skip_min_length = has_image and not self.ignore_pure_media
        valid_len, len_reason = self.filter.check_length(
            text,
            min_len=0 if skip_min_length else self.min_message_length,
            max_len=self.max_message_length,
        )
        if not valid_len:
            if self.debug_log:
                logger.debug(f"[SystemOne] [规则过滤] 消息长度不在有效区间 ({len_reason})")
            return

        # 8. 冷却检查与连续回复限制
        is_cooling, cd_reason = self.cooldown_tracker.is_cooling_down(
            session_id=session_id,
            user_id=sender_id,
            session_cooldown=self.session_cooldown,
            user_cooldown=self.user_cooldown,
            bypass=False,
        )
        if is_cooling:
            logger.info(f"[SystemOne] [频控拦截] 处于冷却中 ({cd_reason})")
            return

        if self.cooldown_tracker.is_continuous_limit_reached(
            session_id=session_id,
            max_continuous=self.max_continuous_replies,
            bypass=False,
        ):
            logger.info(
                f"[SystemOne] [频控拦截] 会话 {session_id} 达到连续回复上限 ({self.max_continuous_replies}轮)，暂停主动发言"
            )
            return

        # 9. 获取最近聊天上下文并组装 SystemOne State
        recent_records = self.context_manager.get_recent_messages(
            session_id=session_id,
            count=self.context_message_count,
            ignore_bots=self.ignore_bots,
            ignore_commands=self.ignore_commands,
        )

        eval_text = text
        if has_image:
            if not text or text == "[图片]":
                eval_text = "[用户发送了一张图片/截图，询问或发起讨论]"
            else:
                eval_text = f"{text} (用户同时发送了图片/截图)"

        state = self.context_manager.build_systemone_state(
            records=recent_records,
            current_message=eval_text,
            current_sender_name=sender_name,
        )

        # 10. 固定问答表：唯一回复来源
        # 先做本地正则召回；未命中直接静默。命中后才交给 Jev 判定是否真提问。
        async for _ in self._handle_qa_reply(
            event=event,
            text=text,
            session_id=session_id,
            is_group=is_group,
            group_id=group_id,
            image_urls=image_urls,
        ):
            yield _
        return

    async def _handle_qa_reply(self, event, text, session_id, is_group, group_id, image_urls):
        """固定问答表回复流程（唯一回复来源）。

        1. 本地正则召回 —— 未命中直接静默，零 API 开销
        2. 命中后把 Q、A 与附加说明一起交给 Jev，二分类判断是否「真提问」
        3. 判定为真提问才回复该条答案
        """
        scope, scope_id = ("group", group_id) if is_group else ("private", session_id)

        # ── 1. 本地正则召回 ───────────────────────────────────
        candidates = self.qa_store.recall_candidates(text, scope, scope_id)
        if not candidates:
            logger.debug("[SystemOne][QA] 正则未命中任何 Q，静默（未调用 Jev）")
            return

        logger.debug(
            "[SystemOne][QA] 召回 %d 条 Q: %s"
            % (len(candidates), " | ".join(c["question"] for c in candidates))
        )

        # 按条目自身的 jev 字段分流：
        #   jev=False 的条目不做相关性判定，正则命中即直接回复（KeyReply 原样行为）
        #   jev=True 的条目交给 Jev 判定是否真提问
        direct = [c for c in candidates if not c.get("jev", True)]
        judged = [c for c in candidates if c.get("jev", True)]

        # 未开启判定的条目先到先得：它们显式要求「命中即回复」
        if direct:
            logger.debug(
                "[SystemOne][QA] 命中未开启 Jev 判定的条目，直接回复：%s" % direct[0]["question"]
            )
            async for _ in self._qa_send_answer(
                event, text, session_id, direct[0], image_urls, llm_mode=False
            ):
                yield _
            return

        if not judged:
            logger.debug("[SystemOne][QA] 候选均未开启 Jev 判定且无直接条目，静默")
            return

        candidates = judged

        # ── 2. Jev 相关性判定（仅在有召回时发生）──────────────
        if not self.systemone_client.is_configured():
            logger.warning("[SystemOne][QA] 已召回候选，但 SystemOne API Key 未配置，保持静默")
            return

        recent_records = self.context_manager.get_recent_messages(
            session_id=session_id,
            count=self.context_message_count,
            ignore_bots=self.ignore_bots,
            ignore_commands=self.ignore_commands,
        )
        state = self.context_manager.build_systemone_state(
            records=recent_records,
            current_message=text,
            current_sender_name=event.get_sender_name() or "群友",
        )

        topic = await self.classifier.match_relevance(
            state=state, candidates=candidates, cache_key_text=text
        )

        if not topic.matched:
            logger.debug(f"[SystemOne][QA] {topic.reason}")
            return

        if not MessageClassifier.is_confidence_sufficient(
            topic.confidence_level, self.qa_min_confidence
        ):
            logger.debug(
                "[SystemOne][QA] 判定相关但置信度 '%s' 未达到阈值 '%s'，保持静默"
                % (topic.confidence_level, self.qa_min_confidence)
            )
            return

        chosen = next((c for c in candidates if c["question"] == topic.question), candidates[0])
        logger.debug(f"[SystemOne][QA] Jev 判定为真提问：{topic.question}")

        # ── 3. 回复该条答案 ───────────────────────────────────
        async for _ in self._qa_send_answer(event, text, session_id, chosen, image_urls):
            yield _

    async def _qa_send_answer(self, event, text, session_id, chosen, image_urls, llm_mode=True):
        """发送某条问答对的答案。

        llm_mode=True 时由 LLM 围绕答案 A 生成自然表述（A 为唯一事实来源）；
        llm_mode=False 时原样发送 A —— 用于 Jev 关闭的 KeyReply 原样模式。
        """
        answer_text = str(chosen.get("answer_text") or "").strip()
        answer_images = list(chosen.get("answer_images") or [])
        if not answer_text and not answer_images:
            logger.warning("[SystemOne][QA] 选中条目的答案为空，保持静默")
            return

        reply_text = answer_text
        if answer_text and llm_mode:
            recent_records = self.context_manager.get_recent_messages(
                session_id=session_id,
                count=self.context_message_count,
                ignore_bots=self.ignore_bots,
                ignore_commands=self.ignore_commands,
            )
            chat_context = self.context_manager.format_context_string(recent_records)
            generated = await self.reply_engine.generate_grounded_reply(
                event=event,
                current_message=text,
                chat_context=chat_context,
                grounded_answer=answer_text,
                model_mode=self.model_mode,
                custom_provider_id=self.custom_provider_id,
                reply_style=self.reply_style,
                reply_length_mode=self.reply_length_mode,
                max_chars=self.max_chars,
                custom_prompt=self.custom_prompt,
                image_urls=image_urls if image_urls else None,
            )
            if generated:
                reply_text = generated
            else:
                logger.warning("[SystemOne][QA] LLM 未生成回复，直接发送原始答案")

        await self._apply_reply_delay()
        self.cooldown_tracker.record_reply_sent(session_id, event.get_sender_id())
        event.stop_event()

        logger.info(
            f"[SystemOne][QA] 已回复会话 {session_id}（问答: {chosen.get('question')}）"
        )

        chain = []
        if reply_text:
            chain.append(Plain(text=reply_text))
        for url in answer_images:
            try:
                chain.append(Image.fromURL(url=url))
            except Exception as e:
                logger.warning(f"[SystemOne][QA] 构造图片组件失败 {url}: {e}")
        yield event.chain_result(chain)

    @filter.command("systemone_status")
    async def command_systemone_status(self, event: AstrMessageEvent):
        """显示 SystemOne 智能自动回复插件运行状态"""
        is_configured = self.systemone_client.is_configured()
        api_health = "正常配置" if is_configured else "未配置 API Key"

        delay_info = (
            f"开启 ({self.reply_delay_mode}, 范围: {self.reply_delay_min}~{self.reply_delay_max}秒 / 固定: {self.reply_delay_fixed}秒)"
            if self.enable_reply_delay
            else "关闭 (秒回)"
        )

        status_text = (
            "=== SystemOne 智能自动回复插件状态 ===\n"
            f"插件总开关: {'开启' if self.enable_plugin else '关闭'}\n"
            f"SystemOne API 状态: {api_health}\n"
            f"SystemOne 判定模型: {self.systemone_model}\n"
            f"SystemOne API 地址: {self.systemone_client.effective_base_url}\n"
            f"群聊自动回复: {'开启' if self.enable_group else '关闭'}\n"
            f"私聊自动回复: {'开启' if self.enable_private else '关闭'}\n"
            f"回复来源: 固定问答表（{MODE_LABELS.get(self.qa_mode, self.qa_mode)}）\n"
            f"回复延时模拟: {delay_info}\n"
            f"模型模式: {self.model_mode} (指定ID: {self.custom_provider_id or '无'})\n"
            f"回复风格: {self.reply_style}\n"
            f"回复长度: {self.reply_length_mode} (上限: {self.max_chars}字)\n"
            f"相关性判定门槛: {self.qa_min_confidence}\n"
            f"会话冷却: {self.session_cooldown}秒 | 用户冷却: {self.user_cooldown}秒\n"
            f"最大连续回复: {self.max_continuous_replies}轮\n"
            f"黑白名单模式: {self.filter_mode}\n"
            "==================================="
        )
        yield event.plain_result(status_text)

    @filter.command("systemone_test")
    async def command_systemone_test(self, event: AstrMessageEvent, message: str = ""):
        """测试固定问答表对指定消息的召回与 Jev 相关性判定（不触发真实回复）。"""
        raw_msg = event.get_message_str() or ""
        match = re.search(r"/?systemone_test\s+(.*)", raw_msg, re.DOTALL | re.IGNORECASE)
        test_text = match.group(1).strip() if match else (message or "").strip()

        if not test_text:
            yield event.plain_result("请在指令后输入要测试的消息内容，例如：/systemone_test 金锭怎么做")
            return

        is_private = event.is_private_chat()
        group_id = str(event.get_group_id() or "")
        session_id = str(event.get_session_id() or group_id or "")
        scope, scope_id = ("private", session_id) if is_private else ("group", group_id)

        candidates = self.qa_store.recall_candidates(test_text, scope, scope_id)
        if not candidates:
            yield event.plain_result(
                "=== 固定问答表命中测试 ===\n"
                f"测试消息: {test_text}\n"
                "正则召回: 未命中任何 Q\n"
                "结论: 保持静默（不会调用 Jev）\n"
                "=========================="
            )
            return

        recalled = "、".join(c["question"] for c in candidates)

        # 按条目 jev 分流：未开启判定的条目命中即直接回复
        direct = [c for c in candidates if not c.get("jev", True)]
        judged = [c for c in candidates if c.get("jev", True)]
        if direct:
            c0 = direct[0]
            yield event.plain_result(
                "=== 固定问答表命中测试 ===\n"
                f"测试消息: {test_text}\n"
                f"正则召回: {recalled}\n"
                f"结论: 命中未开启 Jev 判定的条目「{c0['question']}」→ 直接回复该答案\n"
                f"答案 A: {c0['answer_text'] or '（纯图片答案）'}\n"
                "=========================="
            )
            return

        yield event.plain_result(
            f"正则召回命中 {len(judged)} 条：{recalled}\n"
            f"正在交由 Jev ({self.systemone_model}) 判断是否真提问..."
        )

        if not self.systemone_client.is_configured():
            yield event.plain_result("错误：SystemOne API Key 尚未配置，无法执行相关性判定。")
            return

        state = {"recent_chat": [], "current_message": {"sender": "测试用户", "text": test_text}}
        topic = await self.classifier.match_relevance(state=state, candidates=judged)
        confidence_ok = MessageClassifier.is_confidence_sufficient(
            topic.confidence_level, self.qa_min_confidence
        )

        if topic.matched and confidence_ok:
            chosen = next(
                (c for c in judged if c["question"] == topic.question), judged[0]
            )
            answer = chosen.get("answer_text") or "（纯图片答案）"
            verdict = f"真提问 → 应当回复\n答案 A: {answer}"
        elif topic.matched:
            verdict = (
                f"判定相关，但置信度 '{topic.confidence_level}' "
                f"未达阈值 '{self.qa_min_confidence}' → 静默"
            )
        else:
            verdict = "假命中 / 无关内容 → 静默"

        yield event.plain_result(
            "=== 固定问答表命中测试 ===\n"
            f"测试消息: {test_text}\n"
            f"正则召回: {recalled}\n"
            f"Jev 判定: {verdict}\n"
            f"置信度: {topic.confidence_level} ({topic.confidence_score:.2f})\n"
            f"判定理由: {topic.reason}\n"
            f"是否故障降级: {'是' if topic.is_fallback else '否'}\n"
            "=========================="
        )

    def _migrate_legacy_config(self) -> list:
        """把旧版配置键（typesafe_*）就地搬迁成新版键名（systemone_*）。

        老版本的配置文件里只有旧键，新版只认新键。这里在读取配置之前先搬一次，
        老用户升级后无需重填任何一项。新键已有值时不覆盖，方便手工回退或覆盖。
        """
        migrated: list[str] = []
        for old_key, new_key in LEGACY_CONFIG_KEY_MAP.items():
            old_value = self.config.get(old_key)
            if old_value in (None, ""):
                continue
            if self.config.get(new_key) not in (None, ""):
                continue  # 新键已有值，以新键为准
            try:
                self.config[new_key] = old_value
            except Exception as e:
                logger.warning(
                    f"[SystemOne] 配置键迁移失败 {old_key} → {new_key}: {e}"
                )
                continue
            migrated.append(f"{old_key} → {new_key}")

        if not migrated:
            return migrated

        logger.info("[SystemOne] 已迁移旧版配置键: " + "、".join(migrated))
        save = getattr(self.config, "save_config", None)
        if callable(save):
            try:
                save()
            except Exception as e:
                logger.warning(f"[SystemOne] 迁移后的配置保存失败（本次运行仍生效）: {e}")
        return migrated

    def _migrate_legacy_data_dir(self, base) -> list:
        """把旧插件名数据目录里的问答表补进新目录（只补缺，不覆盖现有数据）。"""
        if not LEGACY_PLUGIN_NAME:
            return []
        legacy = base.parent / LEGACY_PLUGIN_NAME
        if legacy == base or not legacy.is_dir():
            return []
        moved: list[str] = []
        for name in ("qa_tables.json",):
            src, dst = legacy / name, base / name
            if not src.is_file() or dst.exists():
                continue
            try:
                shutil.copy2(src, dst)
            except Exception as e:
                logger.warning(f"[SystemOne] 迁移旧数据文件失败 {src} → {dst}: {e}")
                continue
            moved.append(name)
        if moved:
            logger.info(
                f"[SystemOne] 已从旧数据目录 {legacy} 迁移 "
                + "、".join(moved)
                + "（旧文件保留，确认无误后可自行删除）"
            )
        return moved

    def _plugin_data_dir(self):
        """AstrBot 标准插件数据目录：data/plugin_data/<plugin_name>/。"""
        from pathlib import Path

        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            base = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        except Exception:
            base = Path("data") / "plugin_data" / PLUGIN_NAME
        try:
            base.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"[SystemOne] 创建插件数据目录失败 {base}: {e}")
        # 目录建好后补一次旧目录迁移，之后再交给 QAStore 读取
        self._migrate_legacy_data_dir(base)
        return base

    def _register_web_apis(self):
        """注册配置中心所需的全部 Web API 路由。

        页面通过 window.AstrBotPluginPage.apiGet("config/get") 调用，
        Dashboard 会转发到 /api/plug/astrbot_plugin_systemone_keyreply/config/get。
        """
        plugin_name = PLUGIN_NAME  # 与 metadata.yaml 的 name 一致
        routes = (
            ("console/config", self._api_config_get, ["GET"], "读取插件配置（密钥已掩码）"),
            ("console/config/update", self._api_config_update, ["POST"], "更新插件配置并重载"),
            ("console/config/reset", self._api_config_reset, ["POST"], "将选中字段恢复为默认值"),
            ("console/status", self._api_status, ["GET"], "读取运行状态与生效规则"),
            ("console/try", self._api_try, ["POST"], "在线试判一条消息"),
            ("console/probe", self._api_probe, ["POST"], "测试 SystemOne API 连通性"),
            ("console/qa/list", self._api_qa_list, ["GET"], "读取固定问答表"),
            ("console/qa/save", self._api_qa_save, ["POST"], "保存某个作用域的问答表"),
            ("console/qa/import", self._api_qa_import, ["POST"], "从 KeyReply 数据文件导入"),
            ("console/qa/test", self._api_qa_test, ["POST"], "测试一条消息的命中结果"),
            ("console/qa/delete", self._api_qa_delete, ["POST"], "删除一张问答表"),
        )
        for path, handler, methods, desc in routes:
            try:
                self.context.register_web_api(
                    f"/{plugin_name}/{path}", handler, methods, desc
                )
            except Exception as e:  # 老版本 AstrBot 不支持插件页时不影响插件主体
                logger.warning(f"[SystemOne] 注册 Web API {path} 失败: {e}")

    def _plugin_config_object(self):
        """拿到 AstrBot 持有的 AstrBotConfig（非副本），拿不到时退回实例配置。"""
        try:
            from astrbot.core.star.star import star_registry

            for plugin_md in star_registry:
                if plugin_md.name == PLUGIN_NAME:
                    if plugin_md.config:
                        return plugin_md.config
                    break
        except Exception:
            pass
        return self.config

    async def _save_and_reload_plugin(self) -> bool:
        """持久化配置并尝试热重载插件，返回是否重载成功。"""
        config_obj = self._plugin_config_object()
        try:
            config_obj.save_config()
        except Exception as e:
            logger.warning(f"[SystemOne] 配置保存失败: {e}")
            return False

        try:
            if hasattr(self.context, "reload_plugin"):
                await self.context.reload_plugin(PLUGIN_NAME)
                return True
            if hasattr(self.context, "_star_manager"):
                await self.context._star_manager.reload(PLUGIN_NAME)
                return True
        except Exception as e:
            logger.warning(f"[SystemOne] 插件重载失败: {e}")
            return False

        logger.warning("[SystemOne] 找不到 reload 方法，配置已保存但需手动重载插件")
        return False

    def _audit_config_summary(self) -> dict:
        """供页面展示的配置摘要（不含任何密钥明文）。"""
        return {
            "enable_plugin": bool(self.enable_plugin),
            "enable_group": bool(self.enable_group),
            "enable_private": bool(self.enable_private),
            "configured": self.systemone_client.is_configured(),
            "model": self.systemone_model,
            "reply_style": self.reply_style,
            "reply_length_mode": self.reply_length_mode,
            "qa_min_confidence": self.qa_min_confidence,
            "qa_mode": self.qa_mode,
        }

    async def _api_config_get(self) -> dict:
        """返回当前插件配置（API Key 已掩码）。"""
        config_obj = self._plugin_config_object()
        try:
            raw = dict(config_obj)
        except Exception:
            raw = dict(self.config)

        for field in SECRET_FIELDS:
            if field in raw:
                raw[field] = _mask_secret(raw.get(field))

        raw["_summary"] = self._audit_config_summary()
        return raw

    async def _api_config_update(self):
        """按白名单合并配置：页面没提交的字段保持原值。"""
        from quart import request

        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, dict) or not payload:
            return {"message": "empty config"}, 400

        config_obj = self._plugin_config_object()
        updated: list[str] = []

        for field in _EDITABLE_LIST_FIELDS:
            if field in payload:
                config_obj[field] = _as_text_list(payload[field])
                updated.append(field)

        for field in _EDITABLE_TEXT_FIELDS:
            if field not in payload:
                continue
            value = str(payload[field] or "").strip()
            # 掩码回传 = 不修改，避免把 ******** 写进配置
            if field in SECRET_FIELDS and SECRET_MASK in value:
                continue
            config_obj[field] = value
            updated.append(field)

        for field in _EDITABLE_BOOL_FIELDS:
            if field in payload:
                config_obj[field] = _as_bool(payload[field])
                updated.append(field)

        for field in _EDITABLE_INT_FIELDS:
            if field in payload:
                config_obj[field] = _as_int(
                    payload[field], _as_int(self.config.get(field, 0), 0)
                )
                updated.append(field)

        if not updated:
            return {"message": "no editable field in payload"}, 400

        reloaded = await self._save_and_reload_plugin()
        return {
            "message": "ok",
            "updated": updated,
            "reloaded": reloaded,
            "config": self._audit_config_summary(),
        }

    async def _api_config_reset(self):
        """把选中字段恢复为 _conf_schema.json 中的默认值。"""
        from quart import request

        payload = await request.get_json(silent=True) or {}
        fields = _as_list(payload.get("fields"))
        if not fields:
            return {"message": "no field selected"}, 400

        defaults = self._schema_defaults()
        known = (
            set(_EDITABLE_LIST_FIELDS)
            | set(_EDITABLE_TEXT_FIELDS)
            | set(_EDITABLE_BOOL_FIELDS)
            | set(_EDITABLE_INT_FIELDS)
        )
        config_obj = self._plugin_config_object()
        updated: list[str] = []
        for field in fields:
            name = str(field).strip()
            if name not in known or name not in defaults:
                continue
            value = defaults[name]
            config_obj[name] = list(value) if isinstance(value, list) else value
            updated.append(name)

        if not updated:
            return {"message": "no valid field to reset"}, 400

        reloaded = await self._save_and_reload_plugin()
        return {
            "message": "ok",
            "updated": updated,
            "reloaded": reloaded,
            "config": self._audit_config_summary(),
        }

    @staticmethod
    def _schema_defaults() -> dict:
        """读取 _conf_schema.json 中声明的默认值。"""
        import json
        from pathlib import Path

        path = Path(__file__).parent / "_conf_schema.json"
        try:
            with open(path, "r", encoding="utf-8") as fh:
                schema = json.load(fh)
        except Exception as e:
            logger.warning(f"[SystemOne] 读取 _conf_schema.json 失败: {e}")
            return {}
        return {
            key: item.get("default")
            for key, item in schema.items()
            if isinstance(item, dict) and "default" in item
        }

    async def _api_status(self) -> dict:
        """运行状态快照：等价于 /systemone_status 指令的结构化版本。"""
        delay_info = {
            "enabled": bool(self.enable_reply_delay),
            "mode": self.reply_delay_mode,
            "min": self.reply_delay_min,
            "max": self.reply_delay_max,
            "fixed": self.reply_delay_fixed,
        }

        try:
            active = len(getattr(self.context_manager, "_history", {}) or {})
        except Exception:
            active = 0

        return {
            "version": PLUGIN_VERSION,
            "enable_plugin": bool(self.enable_plugin),
            "enable_group": bool(self.enable_group),
            "enable_private": bool(self.enable_private),
            "api_configured": self.systemone_client.is_configured(),
            "model": self.systemone_model,
            "model_raw": self.systemone_model_raw,
            "custom_model": self.systemone_custom_model,
            "timeout": self.systemone_client.timeout,
            "base_url": self.systemone_base_url,
            "base_url_effective": self.systemone_client.effective_base_url,
            "base_url_supported": bool(self.systemone_client.base_url_supported),
            "failure_mode": self.failure_mode,
            "reply_style": self.reply_style,
            "reply_length_mode": self.reply_length_mode,
            "max_chars": self.max_chars,
            "model_mode": self.model_mode,
            "custom_provider_id": self.custom_provider_id or "",
            "delay": delay_info,
            "session_cooldown": self.session_cooldown,
            "user_cooldown": self.user_cooldown,
            "max_continuous_replies": self.max_continuous_replies,
            "filter_mode": self.filter_mode,
            "context_message_count": self.context_message_count,
            "rate_limit_per_minute": getattr(
                self.systemone_client.rate_limiter, "limit_per_minute", 0
            ),
            "rate_limit_used": self.systemone_client.rate_limiter.current_load(),
            "cache_enabled": bool(self.enable_cache),
            "cache_ttl": self.cache_ttl,
            "cache_size": len(getattr(self.systemone_client.cache, "_cache", {}))
            if getattr(self.systemone_client, "cache", None)
            else 0,
            "debug_log": bool(self.debug_log),
            "active_sessions": active,
            "min_message_length": self.min_message_length,
            "max_message_length": self.max_message_length,
            "qa_mode": self.qa_mode,
            "qa_mode_label": MODE_LABELS.get(self.qa_mode, self.qa_mode),
            "qa_min_confidence": self.qa_min_confidence,
            "qa_summary": self.qa_store.scope_summary(),
        }

    async def _api_try(self):
        """在线试判：正则召回 + Jev 相关性判定（不发送任何消息）。"""
        from quart import request

        payload = await request.get_json(silent=True) or {}
        text = str(payload.get("text") or "").strip()
        if not text:
            return {"ok": False, "message": "请输入要试判的消息内容"}, 400

        scope = str(payload.get("scope") or SCOPE_GLOBAL)
        scope_id = str(payload.get("scope_id") or "").strip()
        if scope not in (SCOPE_GLOBAL, SCOPE_GROUP, SCOPE_PRIVATE):
            return {"message": f"未知作用域: {scope}"}, 400

        candidates = self.qa_store.recall_candidates(text, scope, scope_id)
        if not candidates:
            return {
                "ok": True, "text": text, "recalled": [], "candidate_count": 0,
                "jev": {"matched": False, "reason": "正则未召回任何 Q，不会调用 Jev"},
                "would_reply": False,
            }

        if not self.systemone_client.is_configured():
            return {
                "ok": False, "text": text,
                "message": "已召回候选，但 SystemOne API Key 未配置，无法判定",
            }, 400

        state = {"recent_chat": [], "current_message": {"sender": "测试用户", "text": text}}
        started = time.perf_counter()
        try:
            topic = await self.classifier.match_relevance(
                state=state, candidates=candidates, cache_key_text=text
            )
        except Exception as e:
            logger.error(f"[SystemOne] 在线试判失败: {e}", exc_info=True)
            return {"ok": False, "message": f"试判失败: {e}"}, 500
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        confidence_ok = MessageClassifier.is_confidence_sufficient(
            topic.confidence_level, self.qa_min_confidence
        )
        chosen = None
        if topic.matched:
            chosen = next((c for c in candidates if c["question"] == topic.question), None)

        return {
            "ok": True,
            "text": text,
            "model": self.systemone_model,
            "elapsed_ms": round(elapsed_ms, 1),
            "candidate_count": len(candidates),
            "recalled": [
                {"question": c["question"], "answer": c["answer_text"],
                 "images": c["answer_images"], "hint": c["hint"]}
                for c in candidates
            ],
            "jev": {
                "matched": topic.matched,
                "question": topic.question,
                "confidence_level": topic.confidence_level,
                "confidence_score": round(float(topic.confidence_score), 4),
                "reason": topic.reason,
                "is_fallback": topic.is_fallback,
                "confidence_ok": confidence_ok,
                "answer": (chosen or {}).get("answer_text"),
                "images": (chosen or {}).get("answer_images") or [],
            },
            "would_reply": bool(topic.matched and confidence_ok and not topic.is_fallback),
        }

    async def _api_probe(self):
        """一键测试 SystemOne API 连通性。"""
        if not self.systemone_client:
            return {"ok": False, "message": "客户端未初始化"}, 500

        started = time.perf_counter()
        result = await self.systemone_client.test_api_connection()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return {
            "ok": bool(result.get("ok")),
            "message": result.get("message", ""),
            "model": self.systemone_model,
            "raw_model": getattr(result.get("data"), "model", None),
            "elapsed_ms": round(elapsed_ms, 1),
            "rate_limit_used": self.systemone_client.rate_limiter.current_load(),
        }

    # ── 固定问答表 API ────────────────────────────────────
    async def _api_qa_list(self) -> dict:
        """返回全部问答表、作用域摘要与导入候选路径。"""
        return {
            "tables": self.qa_store.table_list(),
            "summary": self.qa_store.scope_summary(),
            "data_file": str(self.qa_store.path),
            "import_candidates": self.qa_store.find_keyreply_files(),
            "mode": self.qa_mode,
            "mode_label": MODE_LABELS.get(self.qa_mode, self.qa_mode),
            "qa_min_confidence": self.qa_min_confidence,
            "context_message_count": self.context_message_count,
        }

    async def _api_qa_save(self):
        """保存某个作用域的问答表（整体替换）。"""
        from quart import request

        payload = await request.get_json(silent=True) or {}
        scope = str(payload.get("scope") or SCOPE_GLOBAL)
        scope_id = str(payload.get("scope_id") or "").strip()
        entries = payload.get("entries")
        table_key = str(payload.get("key") or "").strip() or None
        name = payload.get("name")
        raw_ids = payload.get("ids")

        if scope not in (SCOPE_GLOBAL, SCOPE_GROUP, SCOPE_PRIVATE):
            return {"message": f"未知作用域: {scope}"}, 400
        if not isinstance(entries, list):
            return {"message": "entries 必须是数组"}, 400

        # 多群一域：ids 为服务 ID 列表；未传时退回单个 scope_id
        ids: Optional[list] = None
        if isinstance(raw_ids, list):
            ids = [str(x).strip() for x in raw_ids if str(x).strip()]
        elif scope_id:
            ids = [scope_id]

        if scope in (SCOPE_GROUP, SCOPE_PRIVATE) and not ids:
            return {"message": "群聊/私聊专属表至少需要一个 ID（群号或用户 QQ）"}, 400

        table = self.qa_store.replace_table(
            scope,
            scope_id or (ids[0] if ids else ""),
            entries,
            ids=ids,
            name=None if name is None else str(name),
            key=table_key,
        )
        # 保存时按答案指纹重新分组，使「多个 Q 指向同一个 A」在页面上可直接体现
        regroup_answers(table)
        if not self.qa_store.save():
            return {"message": "写入数据文件失败，请检查目录权限"}, 500

        logger.info(
            f"[SystemOne][QA] 已保存问答表 {table.label}，共 {len(table.entries)} 条，"
            f"服务 {len(table.ids)} 个会话"
        )
        return {
            "message": "ok",
            "table": {
                "key": table.key,
                "scope": table.scope,
                "scope_id": table.scope_id,
                "ids": list(table.ids),
                "name": table.name,
                # 用已解析答案的形态回显，页面的草稿与列表才不会显示成空答案
                "entries": self.qa_store.serialize_entries(table),
            },
            "tables": self.qa_store.table_list(),
            "summary": self.qa_store.scope_summary(),
        }

    async def _api_qa_import(self):
        """把 KeyReply 的问答表复制到本插件的数据目录。

        默认自动探测来源文件；payload 传 replace=true 时整体替换目标表，
        否则按问题去重后合并（追加）。
        """
        from quart import request
        from pathlib import Path

        payload = await request.get_json(silent=True) or {}
        scope = str(payload.get("scope") or SCOPE_GLOBAL)
        scope_id = str(payload.get("scope_id") or "").strip()
        path_text = str(payload.get("path") or "").strip()
        replace = bool(payload.get("replace", False))

        if scope not in (SCOPE_GLOBAL, SCOPE_GROUP, SCOPE_PRIVATE):
            return {"message": f"未知作用域: {scope}"}, 400
        if scope in (SCOPE_GROUP, SCOPE_PRIVATE) and not scope_id:
            return {"message": "群聊/私聊专属表必须提供 scope_id"}, 400

        result = self.qa_store.copy_keyreply_into_store(
            Path(path_text) if path_text else None,
            scope=scope,
            scope_id=scope_id,
            replace=replace,
        )
        # 找不到来源文件属于「没得导」，用 404 让页面给出更准确的提示
        status = 200 if result.get("ok") else (404 if not result.get("source_path") else 400)
        result["summary"] = self.qa_store.scope_summary()
        result["tables"] = self.qa_store.table_list()
        if result.get("ok"):
            logger.info(
                f"[SystemOne][QA] 已从 {result.get('source_path')} 复制问答表到 "
                f"{result.get('target_path')}（{result.get('message')}）"
            )
        return result, status

    async def _api_qa_delete(self):
        """删除一张问答表（按存储主键精确定位）。"""
        from quart import request

        payload = await request.get_json(silent=True) or {}
        key = str(payload.get("key") or "").strip()
        scope = str(payload.get("scope") or "")
        scope_id = str(payload.get("scope_id") or "").strip()

        if not key and not scope_id:
            return {"message": "需要提供 key，或 scope + scope_id"}, 400
        if key and key == SCOPE_GLOBAL:
            return {"message": "全局默认表不可删除，可直接清空其内容"}, 400

        ok = self.qa_store.delete_table_by_key(key) if key else self.qa_store.delete_table(scope, scope_id)
        if not ok:
            return {"message": "未找到对应的问答表"}, 404
        if not self.qa_store.save():
            return {"message": "写入数据文件失败，请检查目录权限"}, 500

        logger.info(f"[SystemOne][QA] 已删除问答表: {key or (scope + ':' + scope_id)}")
        return {
            "message": "ok",
            "tables": self.qa_store.table_list(),
            "summary": self.qa_store.scope_summary(),
        }

    async def _api_qa_test(self):
        """测试一条消息：正则召回 + Jev 相关性判定（不发送任何消息）。"""
        from quart import request

        payload = await request.get_json(silent=True) or {}
        text = str(payload.get("text") or "").strip()
        if not text:
            return {"ok": False, "message": "请输入要测试的消息内容"}, 400

        scope = str(payload.get("scope") or SCOPE_GLOBAL)
        scope_id = str(payload.get("scope_id") or "").strip()
        if scope not in (SCOPE_GLOBAL, SCOPE_GROUP, SCOPE_PRIVATE):
            return {"message": f"未知作用域: {scope}"}, 400

        candidates = self.qa_store.recall_candidates(text, scope, scope_id)
        recalled = [
            {"question": c["question"], "answer": c["answer_text"],
             "images": c["answer_images"], "hint": c["hint"],
             "table_label": c["table_label"]}
            for c in candidates
        ]

        # 未召回：直接给结论，不调用 Jev
        if not candidates:
            return {
                "ok": True, "text": text, "scope": scope, "scope_id": scope_id,
                "mode": self.qa_mode,
                "mode_label": MODE_LABELS.get(self.qa_mode, self.qa_mode),
                "recalled": [], "candidate_count": 0,
                "classic": None,
                "jev": {"matched": False, "reason": "正则未召回任何 Q，不会调用 Jev"},
                "would_reply": False,
            }

        # 按条目分流：未开启判定的条目直接命中即回复；其余交给 Jev
        direct = [c for c in candidates if not c.get("jev", True)]
        judged = [c for c in candidates if c.get("jev", True)]

        classic_view = None
        if direct:
            c0 = direct[0]
            classic_view = {
                "question": c0["question"], "answer": c0["answer_text"],
                "images": c0["answer_images"], "table_label": c0["table_label"],
            }

        jev_view = None
        if judged and self.systemone_client.is_configured():
            state = {"recent_chat": [], "current_message": {"sender": "测试用户", "text": text}}
            started = time.perf_counter()
            topic = await self.classifier.match_relevance(state=state, candidates=judged)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            chosen = None
            if topic.matched:
                chosen = next((c for c in judged if c["question"] == topic.question), None)
            jev_view = {
                "matched": topic.matched,
                "question": topic.question,
                "confidence_level": topic.confidence_level,
                "confidence_score": round(float(topic.confidence_score), 4),
                "reason": topic.reason,
                "is_fallback": topic.is_fallback,
                "answer": (chosen or {}).get("answer_text"),
                "images": (chosen or {}).get("answer_images") or [],
                "confidence_ok": MessageClassifier.is_confidence_sufficient(
                    topic.confidence_level, self.qa_min_confidence
                ),
                "elapsed_ms": round(elapsed_ms, 1),
            }
        elif not judged:
            jev_view = {"matched": False, "reason": "召回条目均未开启 Jev 判定，正则命中即直接回复"}
        else:
            jev_view = {"matched": False, "reason": "SystemOne API Key 未配置，无法进行相关性判定"}

        would_reply = bool(classic_view) or bool(
            jev_view and jev_view.get("matched") and jev_view.get("confidence_ok")
        )

        return {
            "ok": True, "text": text, "scope": scope, "scope_id": scope_id,
            "mode": self.qa_mode,
            "mode_label": MODE_LABELS.get(self.qa_mode, self.qa_mode),
            "recalled": recalled,
            "candidate_count": len(candidates),
            "classic": classic_view,
            "jev": jev_view,
            "would_reply": would_reply,
        }

    # ── 固定问答表 API END ────────────────────────────────

    async def terminate(self):
        """插件卸载或停用时的资源释放"""
        logger.info("[SystemOne] 插件正在卸载，清理资源...")
        if self.systemone_client.cache:
            self.systemone_client.cache.clear()
        logger.info("[SystemOne] 插件卸载完成")
