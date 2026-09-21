import logging
import sys
import time
import asyncio
import re
from pathlib import Path
from typing import AsyncGenerator, Any

plugin_dir = str(Path(__file__).parent.resolve())
if plugin_dir not in sys.path:
    sys.path.insert(0, plugin_dir)

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig
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
    from .typesafe_client import TypeSafeClientWrapper
    from .classifier import MessageClassifier, REPLY_TYPE_NAMES
    from .reply_engine import ReplyEngine
    from .context_manager import ContextManager
    from .filters import MessageFilter
    from .utils import (
        CooldownTracker,
        check_probability,
        calculate_reply_delay,
        normalize_failure_mode,
        normalize_confidence_level,
        normalize_model_mode,
        normalize_reply_style,
        normalize_reply_length_mode,
        normalize_filter_mode,
        normalize_force_reply_mode,
        normalize_typesafe_model,
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
    from typesafe_client import TypeSafeClientWrapper
    from classifier import MessageClassifier, REPLY_TYPE_NAMES
    from reply_engine import ReplyEngine
    from context_manager import ContextManager
    from filters import MessageFilter
    from utils import (
        CooldownTracker,
        check_probability,
        calculate_reply_delay,
        normalize_failure_mode,
        normalize_confidence_level,
        normalize_model_mode,
        normalize_reply_style,
        normalize_reply_length_mode,
        normalize_filter_mode,
        normalize_force_reply_mode,
        normalize_typesafe_model,
    )

logger = logging.getLogger("astrbot")


CQ_IMAGE_REGEX = re.compile(r"\[CQ:image,[^\]]*?(?:url|file)=([^,\]]+)", re.IGNORECASE)


# ═══════════════════════════════════════════════════════════════
# WebUI 配置中心：可编辑字段白名单与取值归一化
# 页面与 _conf_schema.json 共用同一份 AstrBot 配置，字段名与默认值保持一致。
# ═══════════════════════════════════════════════════════════════

SECRET_MASK = "********"
SECRET_FIELDS = ("typesafe_api_key",)

_EDITABLE_LIST_FIELDS = (
    "allowed_reply_types",
    "session_whitelist",
    "session_blacklist",
    "user_whitelist",
    "user_blacklist",
    "force_reply_keywords",
    "ignore_keywords",
)

_EDITABLE_TEXT_FIELDS = (
    "reply_source",
    "qa_min_confidence",
    "qa_import_path",
    "typesafe_api_key",
    "typesafe_model",
    "typesafe_custom_model",
    "failure_mode",
    "min_confidence",
    "model_mode",
    "custom_provider_id",
    "reply_length_mode",
    "reply_style",
    "custom_prompt",
    "reply_delay_mode",
    "filter_mode",
    "force_reply_mode",
    "force_trigger_regex",
    "ignore_regex",
)

_EDITABLE_BOOL_FIELDS = (
    "enable_jev_topic",
    "qa_fallback_to_llm",
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
    "typesafe_timeout",
    "max_chars",
    "reply_delay_min",
    "reply_delay_max",
    "reply_delay_fixed",
    "context_message_count",
    "session_cooldown",
    "user_cooldown",
    "max_continuous_replies",
    "reply_probability",
    "min_message_length",
    "max_message_length",
    "rate_limit_per_minute",
    "cache_ttl",
)


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


@register(
    "astrbot_plugin_typesafe_autoreply",
    "AstrBot & TypeSafe Community",
    "使用 TypeSafe AI 判断聊天消息是否需要 AI 主动参与，并调用 AstrBot 已配置的大语言模型生成自然回复。",
    "1.0.5",
    "https://github.com/mmyddd/astrbot_plugin_typesafe_autoreply",
)
class TypeSafeAutoReplyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config or {}

        # 1. 基础配置
        self.enable_plugin = self.config.get("enable_plugin", True)
        self.enable_group = self.config.get("enable_group", True)
        self.enable_private = self.config.get("enable_private", False)

        # 2. TypeSafe AI 配置 (支持中英文双语选项自动归一化与模型选择)
        self.typesafe_api_key = self.config.get("typesafe_api_key", "")
        self.typesafe_timeout = self.config.get("typesafe_timeout", 10)
        self.typesafe_model_raw = self.config.get(
            "typesafe_model", "jev-latest (推荐最新旗舰)"
        )
        self.typesafe_custom_model = self.config.get("typesafe_custom_model", "")
        self.typesafe_model = normalize_typesafe_model(
            self.typesafe_model_raw, self.typesafe_custom_model
        )

        self.failure_mode = normalize_failure_mode(
            self.config.get("failure_mode", "静默，不回复")
        )
        self.min_confidence = normalize_confidence_level(
            self.config.get("min_confidence", "中")
        )
        raw_allowed_types = self.config.get(
            "allowed_reply_types",
            [
                "明确提问",
                "求助",
                "讨论",
                "技术问题",
                "信息查询",
                "建议请求",
            ],
        )
        if isinstance(raw_allowed_types, list):
            # 将其中的英文键名统一转换为中文名称
            norm_types = []
            for t in raw_allowed_types:
                t_str = str(t).strip()
                if not t_str:
                    continue
                norm_types.append(REPLY_TYPE_NAMES.get(t_str, t_str))

            # 如果配置使用的是旧版本的默认 5 项 (无论中英文)，自动平滑补齐 "讨论"
            has_discussion = any(t in ("讨论", "discussion") for t in norm_types)
            if not has_discussion:
                old_default_set = {
                    "explicit_question",
                    "seek_help",
                    "technical_issue",
                    "info_query",
                    "recommendation",
                    "明确提问",
                    "求助",
                    "技术问题",
                    "信息查询",
                    "建议请求",
                }
                if set(raw_allowed_types).issubset(old_default_set) or not norm_types:
                    norm_types.append("讨论")
            self.allowed_reply_types = norm_types
        else:
            self.allowed_reply_types = [
                "明确提问",
                "求助",
                "讨论",
                "技术问题",
                "信息查询",
                "建议请求",
            ]

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
        self.reply_probability = self.config.get("reply_probability", 100)

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

        # 8. 高级过滤
        self.min_message_length = self.config.get("min_message_length", 2)
        self.max_message_length = self.config.get("max_message_length", 2000)
        self.ignore_commands = self.config.get("ignore_commands", True)
        self.ignore_bots = self.config.get("ignore_bots", True)
        self.ignore_pure_media = self.config.get("ignore_pure_media", True)
        self.force_reply_keywords = self.config.get(
            "force_reply_keywords", ["有人知道", "怎么解决", "求助", "请问", "怎么办"]
        )
        self.force_reply_mode = normalize_force_reply_mode(
            self.config.get("force_reply_mode", "进入 TypeSafe 判断")
        )
        self.ignore_keywords = self.config.get(
            "ignore_keywords", ["机器人别说话", "不用机器人回答"]
        )
        self.force_trigger_regex = self.config.get("force_trigger_regex", "")
        self.ignore_regex = self.config.get("ignore_regex", "")
        self.force_trigger_regex_pattern = None
        if self.force_trigger_regex and self.force_trigger_regex.strip():
            try:
                self.force_trigger_regex_pattern = re.compile(
                    self.force_trigger_regex.strip(), re.IGNORECASE
                )
            except re.error as e:
                logger.warning(f"[TypeSafe] 强制触发正则编译失败: {e}")

        self.ignore_regex_pattern = None
        if self.ignore_regex and self.ignore_regex.strip():
            try:
                self.ignore_regex_pattern = re.compile(
                    self.ignore_regex.strip(), re.IGNORECASE
                )
            except re.error as e:
                logger.warning(f"[TypeSafe] 忽略正则表达式编译失败: {e}")

        # 9. API 限流、缓存与调试
        self.rate_limit_per_minute = self.config.get("rate_limit_per_minute", 60)
        self.enable_cache = self.config.get("enable_cache", False)
        self.cache_ttl = self.config.get("cache_ttl", 60)
        self.debug_log = self.config.get("debug_log", False)

        # 初始化子模块
        self.typesafe_client = TypeSafeClientWrapper(
            api_key=self.typesafe_api_key,
            timeout=self.typesafe_timeout,
            rate_limit_per_minute=self.rate_limit_per_minute,
            enable_cache=self.enable_cache,
            cache_ttl=self.cache_ttl,
            failure_mode=self.failure_mode,
            model=self.typesafe_model,
        )
        self.classifier = MessageClassifier(self.typesafe_client)
        self.reply_engine = ReplyEngine(self.context)
        self.context_manager = ContextManager(max_history_per_session=20)
        self.cooldown_tracker = CooldownTracker()
        self.filter = MessageFilter()

        # 10. 固定问答表（KeyReply 兼容）
        self.reply_source = str(self.config.get("reply_source", "大模型自由回复"))
        self.use_qa_table = "固定问答表" in self.reply_source or "keyreply" in self.reply_source.lower()
        self.enable_jev_topic = bool(self.config.get("enable_jev_topic", True))
        self.qa_min_confidence = normalize_confidence_level(
            self.config.get("qa_min_confidence", "中")
        )
        self.qa_fallback_to_llm = bool(self.config.get("qa_fallback_to_llm", False))
        self.qa_import_path = str(self.config.get("qa_import_path", "") or "")
        self.qa_mode = resolve_mode(self.enable_jev_topic)
        self.qa_store = QAStore(self._plugin_data_dir())

        # 11. WebUI 配置中心（Dashboard 插件页）后端路由
        self._register_web_apis()

        logger.info(
            f"[TypeSafe] 插件已加载. 启用状态: {self.enable_plugin}, "
            f"API配置: {self.typesafe_client.is_configured()}, 模型: {self.typesafe_model}, "
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
            logger.info(f"[TypeSafe] 模拟思考打字延时 {delay:.2f} 秒...")
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
            logger.debug(f"[TypeSafe] 提取消息组件图片失败: {e}")

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
                logger.debug(f"[TypeSafe] [规则过滤] 忽略指令消息: \"{display_msg}\"")
            return

        # 5. 是否纯媒体/图片/表情空文本消息？
        is_pure_media = self.filter.is_pure_media_message(text)
        if self.ignore_pure_media and (
            is_pure_media or (has_image and (not text or text == "[图片]"))
        ):
            if self.debug_log:
                logger.debug(f"[TypeSafe] [规则过滤] 忽略纯媒体/表情消息: \"{display_msg}\"")
            return

        # 日志记录进入决策流水线的消息
        logger.info(
            f"[TypeSafe] [1/4 收到消息] 会话: {session_id} | 发送者: {sender_name}({sender_id}) | 内容: \"{display_msg}\""
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
            logger.info(f"[TypeSafe] [规则过滤] 黑白名单拦截 ({wl_reason})")
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
                logger.debug(f"[TypeSafe] [规则过滤] 消息长度不在有效区间 ({len_reason})")
            return

        # 8. 忽略关键词与忽略正则检查（优先使用预编译 Pattern）
        if self.filter.matches_keywords(text, self.ignore_keywords):
            logger.info("[TypeSafe] [规则过滤] 命中忽略关键词，保持静默")
            return
        if self.ignore_regex_pattern:
            if self.filter.matches_regex(text, self.ignore_regex_pattern):
                logger.info("[TypeSafe] [规则过滤] 命中忽略正则表达式，保持静默")
                return
        elif self.ignore_regex and self.filter.matches_regex(text, self.ignore_regex):
            logger.info("[TypeSafe] [规则过滤] 命中忽略正则表达式，保持静默")
            return

        # 9. 冷却检查与连续回复限制
        is_cooling, cd_reason = self.cooldown_tracker.is_cooling_down(
            session_id=session_id,
            user_id=sender_id,
            session_cooldown=self.session_cooldown,
            user_cooldown=self.user_cooldown,
            bypass=False,
        )
        if is_cooling:
            logger.info(f"[TypeSafe] [频控拦截] 处于冷却中 ({cd_reason})")
            return

        if self.cooldown_tracker.is_continuous_limit_reached(
            session_id=session_id,
            max_continuous=self.max_continuous_replies,
            bypass=False,
        ):
            logger.info(
                f"[TypeSafe] [频控拦截] 会话 {session_id} 达到连续回复上限 ({self.max_continuous_replies}轮)，暂停主动发言"
            )
            return

        # 12. 强制触发关键词与正则检查
        force_reply = self.filter.matches_keywords(
            text, self.force_reply_keywords
        ) or (
            self.filter.matches_regex(text, self.force_trigger_regex_pattern)
            if self.force_trigger_regex_pattern
            else (
                self.filter.matches_regex(text, self.force_trigger_regex)
                if self.force_trigger_regex
                else False
            )
        )

        if force_reply and self.force_reply_mode == "direct_reply":
            logger.info("[TypeSafe] [关键词直通] 命中强制回复关键词/正则，直接生成回复")
            recent_msgs = self.context_manager.get_recent_messages(
                session_id,
                count=self.context_message_count,
                ignore_bots=self.ignore_bots,
                ignore_commands=self.ignore_commands,
            )
            chat_context = self.context_manager.format_context_string(recent_msgs)

            reply_text = await self.reply_engine.generate_reply(
                event=event,
                current_message=text,
                chat_context=chat_context,
                model_mode=self.model_mode,
                custom_provider_id=self.custom_provider_id,
                reply_style=self.reply_style,
                reply_length_mode=self.reply_length_mode,
                max_chars=self.max_chars,
                custom_prompt=self.custom_prompt,
                image_urls=image_urls if image_urls else None,
            )
            if reply_text:
                await self._apply_reply_delay()
                self.cooldown_tracker.record_reply_sent(session_id, sender_id)
                event.stop_event()
                yield event.plain_result(reply_text)
            return

        # 13. 获取最近聊天上下文并组装 TypeSafe State
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

        state = self.context_manager.build_typesafe_state(
            records=recent_records,
            current_message=eval_text,
            current_sender_name=sender_name,
        )

        # 13.5 固定问答表模式：完全接管回复来源
        # 开启后不再走 TypeSafe「是否需要回复」的两阶段流程，改为 QA 表命中驱动。
        if self.use_qa_table:
            logger.debug(
                f"[TypeSafe][QA] 固定问答表模式 ({MODE_LABELS.get(self.qa_mode, self.qa_mode)}) "
                f"| 会话: {session_id}"
            )
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

        # 14. TypeSafe AI 结构化判断
        logger.info(
            f"[TypeSafe] [2/4 调用TypeSafe] 正在调用 TypeSafe AI (模型: {self.typesafe_model}) 分析消息意图与是否需要回复: \"{display_msg}\""
        )

        decision = await self.classifier.classify_message(
            state=state,
            cache_key_text=text if (text and text != "[图片]") else eval_text,
        )

        logger.info(
            f"[TypeSafe] [3/4 TypeSafe判决结果] 是否需主动回复: {'【是】' if decision.should_reply else '【否】'}, "
            f"意图: {decision.reply_type_display} ({decision.reply_type}), "
            f"置信度: {decision.confidence_level} ({decision.confidence_score:.2f}), "
            f"紧急度: {decision.urgency}, 判定理由: \"{decision.reason}\""
        )

        # 15. should_reply 是否为 True？
        if not decision.should_reply:
            logger.info(
                f"[TypeSafe] [4/4 决策保持静默] TypeSafe 判定无需主动回复: {decision.reason}"
            )
            return

        # 16. 类型是否在允许列表中？
        is_type_allowed = MessageClassifier.is_reply_type_allowed(
            decision.reply_type, self.allowed_reply_types
        )
        if not is_type_allowed:
            chinese_allowed = [
                REPLY_TYPE_NAMES.get(t, t) for t in self.allowed_reply_types
            ]
            logger.info(
                f"[TypeSafe] [4/4 决策保持静默] 意图类型 '{decision.reply_type_display}' 未包含在允许回复列表中 ({chinese_allowed})"
            )
            return

        # 17. 置信度是否达标？
        if not MessageClassifier.is_confidence_sufficient(
            decision.confidence_level, self.min_confidence
        ):
            logger.info(
                f"[TypeSafe] [4/4 决策保持静默] 置信度 '{decision.confidence_level}' 未达到最低阈值 '{self.min_confidence}'"
            )
            return

        # 18. 回复概率检查
        if not check_probability(self.reply_probability):
            logger.info(f"[TypeSafe] [4/4 决策保持静默] 回复概率未命中 (设定为 {self.reply_probability}%)")
            return

        # 19. 调用 AstrBot LLM 生成回复
        logger.info(
            f"[TypeSafe] [4/4 触发主动回复] 综合判定通过，正在调用 LLM Provider ({self.custom_provider_id or '当前会话模型'}) 生成自然回复..."
        )
        chat_context = self.context_manager.format_context_string(recent_records)
        llm_current_msg = (
            "[用户发送了一张图片，请结合图片内容与上下文进行回复]"
            if (has_image and (not text or text == "[图片]"))
            else text
        )
        reply_text = await self.reply_engine.generate_reply(
            event=event,
            current_message=llm_current_msg,
            chat_context=chat_context,
            model_mode=self.model_mode,
            custom_provider_id=self.custom_provider_id,
            reply_style=self.reply_style,
            reply_length_mode=self.reply_length_mode,
            max_chars=self.max_chars,
            custom_prompt=self.custom_prompt,
            image_urls=image_urls if image_urls else None,
        )

        if not reply_text:
            logger.warning("[TypeSafe] LLM 未能生成有效回复内容")
            return

        # 20. 执行延时、发送回复并记录冷却、阻止事件向后传播
        await self._apply_reply_delay()
        self.cooldown_tracker.record_reply_sent(session_id, sender_id)
        event.stop_event()
        logger.info(f"[TypeSafe] 回复已成功发送至会话 ({session_id})")
        yield event.plain_result(reply_text)

    # ═══════════════════════════════════════════════════════════
    # 固定问答表（KeyReply）回复路径
    # ═══════════════════════════════════════════════════════════

    async def _handle_qa_reply(
        self,
        event: AstrMessageEvent,
        text: str,
        session_id: str,
        is_group: bool,
        group_id: str,
        image_urls: list,
    ):
        """固定问答表的完整处理流程（返回 True 表示已处理并结束）。"""
        scope, scope_id = ("group", group_id) if is_group else ("private", session_id)
        if self.qa_mode == MODE_JEV:
            async for _ in self._handle_qa_jev(event, text, session_id, scope, scope_id, image_urls):
                yield _
        else:
            async for _ in self._handle_qa_classic(event, text, session_id, scope, scope_id):
                yield _

    async def _handle_qa_classic(self, event, text, session_id, scope, scope_id):
        """经典模式：正则匹配 QA 表，命中即原样发送固定答案。"""
        match = self.qa_store.find_reply(text, scope, scope_id)
        if not match:
            if self.debug_log:
                logger.debug("[TypeSafe][QA] 经典模式未命中任何问答，保持静默")
            return

        entry = match.get("entry") or {}
        logger.debug(f"[TypeSafe][QA] [经典模式] {describe_match(match)} | 会话: {session_id}")

        reply_text = resolved_text(match)
        images = resolved_images(match)
        if not reply_text and not images:
            logger.warning("[TypeSafe][QA] 命中的问答对内容为空，保持静默")
            return

        await self._apply_reply_delay()
        self.cooldown_tracker.record_reply_sent(session_id, event.get_sender_id())
        event.stop_event()

        chain = []
        if reply_text:
            chain.append(Plain(text=reply_text))
        for url in images:
            try:
                chain.append(Image.fromURL(url=url))
            except Exception as e:
                logger.warning(f"[TypeSafe][QA] 构造图片组件失败 {url}: {e}")
        if chain:
            yield event.chain_result(chain)

    async def _handle_qa_jev(self, event, text, session_id, scope, scope_id, image_urls):
        """Jev 话题模式（两阶段）：

        阶段一（本地、零开销）：用 KeyReply 的 % 通配正则做**召回**。
            没有命中任何 Q 就直接静默返回——不会调用 Jev，也不会走到任何后续检测。
        阶段二（仅在召回非空时触发）：把**召回出来的这几条 Q** 作为候选交给 Jev，
            由 Jev 确认/消歧到底属于哪一条，再做置信度等后续检测。
        """
        # ── 阶段一：正则召回（本地，不打任何 API）────────────────
        hits = self.qa_store.find_all_replies(text, scope, scope_id)
        if not hits:
            # 每条未命中的消息都会走到这里，属于逐条噪声，只在 debug 层输出
            logger.debug("[TypeSafe][QA] [召回] 未命中任何 Q，静默（未调用 Jev）")
            return

        recalled_questions = []
        for h in hits:
            q = question_of(h.get("entry"))
            if q and q not in recalled_questions:
                recalled_questions.append(q)

        logger.debug(
            f"[TypeSafe][QA] [召回] 命中 {len(hits)} 条 Q，触发 Jev 判定: "
            f"{' | '.join(recalled_questions)}"
        )

        # ── 阶段二：Jev 确认/消歧（仅有召回时才发生）────────────
        if not self.typesafe_client.is_configured():
            logger.warning(
                "[TypeSafe][QA] 已召回候选，但 TypeSafe API Key 未配置，无法进行 Jev 确认；"
                "按当前配置保持静默"
            )
            return

        # 命中即触发 Jev：即使只有一条候选也交给 Jev 确认，
        # 这样置信度门槛等「后面的检测」对所有命中一视同仁。
        recent_records = self.context_manager.get_recent_messages(
            session_id=session_id,
            count=self.context_message_count,
            ignore_bots=self.ignore_bots,
            ignore_commands=self.ignore_commands,
        )
        state = self.context_manager.build_typesafe_state(
            records=recent_records,
            current_message=text,
            current_sender_name=event.get_sender_name() or "群友",
        )

        logger.debug(
            f"[TypeSafe][QA] [Jev] 正在确认话题 (候选 {len(recalled_questions)} 条, "
            f"模型: {self.typesafe_model})"
        )
        topic = await self.classifier.match_topic(
            state=state,
            questions=recalled_questions,
            cache_key_text=text,
        )

        if not topic.matched:
            logger.debug(f"[TypeSafe][QA] [Jev] 未确认任何候选: {topic.reason}")
            if self.qa_fallback_to_llm:
                async for _ in self._qa_fallback_llm(event, text, session_id, recent_records, image_urls):
                    yield _
            return

        if not MessageClassifier.is_confidence_sufficient(
            topic.confidence_level, self.qa_min_confidence
        ):
            logger.debug(
                f"[TypeSafe][QA] [Jev] 确认「{topic.question}」但置信度 "
                f"'{topic.confidence_level}' 未达到阈值 '{self.qa_min_confidence}'，保持静默"
            )
            return

        logger.debug(
            f"[TypeSafe][QA] [Jev] 确认话题「{topic.question}」"
            f" (置信度 {topic.confidence_level} {topic.confidence_score:.2f})"
        )

        chosen = self.qa_store.find_reply_by_question(topic.question, scope, scope_id)
        if not chosen:
            logger.warning(f"[TypeSafe][QA] 确认了话题但未能取回答案: {topic.question}")
            return

        async for _ in self._qa_generate_from_hit(
            event, text, session_id, scope, scope_id, chosen, image_urls
        ):
            yield _

    async def _qa_generate_from_hit(
        self, event, text, session_id, scope, scope_id, match, image_urls
    ):
        """已经确定使用哪条问答对：组装上下文，让 LLM 围绕答案 A 生成并发送。"""
        entry = match.get("entry") or {}
        grounded = resolved_text(match)
        if not grounded.strip():
            logger.warning("[TypeSafe][QA] 选中条目的答案为空，保持静默")
            return

        recent_records = self.context_manager.get_recent_messages(
            session_id=session_id,
            count=self.context_message_count,
            ignore_bots=self.ignore_bots,
            ignore_commands=self.ignore_commands,
        )

        chat_context = self.context_manager.format_context_string(recent_records)
        reply_text = await self.reply_engine.generate_grounded_reply(
            event=event,
            current_message=text,
            chat_context=chat_context,
            grounded_answer=grounded,
            model_mode=self.model_mode,
            custom_provider_id=self.custom_provider_id,
            reply_style=self.reply_style,
            reply_length_mode=self.reply_length_mode,
            max_chars=self.max_chars,
            custom_prompt=self.custom_prompt,
            image_urls=image_urls if image_urls else None,
        )

        if not reply_text:
            logger.warning("[TypeSafe][QA] LLM 未能围绕固定答案生成回复，回退为直接发送答案")
            reply_text = grounded

        await self._apply_reply_delay()
        self.cooldown_tracker.record_reply_sent(session_id, event.get_sender_id())
        event.stop_event()

        # 真正发出回复属于低频关键事件，保留在 INFO
        logger.info(
            f"[TypeSafe][QA] 已回复会话 {session_id}（话题: {question_of(entry)}）"
        )

        chain = [Plain(text=reply_text)]
        for url in resolved_images(match):
            try:
                chain.append(Image.fromURL(url=url))
            except Exception as e:
                logger.warning(f"[TypeSafe][QA] 构造图片组件失败 {url}: {e}")
        yield event.chain_result(chain)

    async def _qa_fallback_llm(self, event, text, session_id, recent_records, image_urls):
        """QA 未命中且开启回退时，走原有的大模型自由回复。"""
        logger.debug("[TypeSafe][QA] 未命中问答表，按配置回退到大模型自由回复")
        chat_context = self.context_manager.format_context_string(recent_records)
        reply_text = await self.reply_engine.generate_reply(
            event=event,
            current_message=text,
            chat_context=chat_context,
            model_mode=self.model_mode,
            custom_provider_id=self.custom_provider_id,
            reply_style=self.reply_style,
            reply_length_mode=self.reply_length_mode,
            max_chars=self.max_chars,
            custom_prompt=self.custom_prompt,
            image_urls=image_urls if image_urls else None,
        )
        if reply_text:
            await self._apply_reply_delay()
            self.cooldown_tracker.record_reply_sent(session_id, event.get_sender_id())
            event.stop_event()
            yield event.plain_result(reply_text)

    @filter.command("typesafe_status")
    async def command_typesafe_status(self, event: AstrMessageEvent):
        """显示 TypeSafe 智能自动回复插件运行状态"""
        is_configured = self.typesafe_client.is_configured()
        api_health = "正常配置" if is_configured else "未配置 API Key"

        active_types_str = "、".join(
            [REPLY_TYPE_NAMES.get(t, t) for t in self.allowed_reply_types]
        )

        delay_info = (
            f"开启 ({self.reply_delay_mode}, 范围: {self.reply_delay_min}~{self.reply_delay_max}秒 / 固定: {self.reply_delay_fixed}秒)"
            if self.enable_reply_delay
            else "关闭 (秒回)"
        )

        status_text = (
            "=== TypeSafe 智能自动回复插件状态 ===\n"
            f"插件总开关: {'开启' if self.enable_plugin else '关闭'}\n"
            f"TypeSafe API 状态: {api_health}\n"
            f"TypeSafe 判定模型: {self.typesafe_model}\n"
            f"群聊自动回复: {'开启' if self.enable_group else '关闭'}\n"
            f"私聊自动回复: {'开启' if self.enable_private else '关闭'}\n"
            f"回复来源: {MODE_LABELS.get(self.qa_mode, self.qa_mode) if self.use_qa_table else '大模型自由回复'}\n"
            f"回复延时模拟: {delay_info}\n"
            f"模型模式: {self.model_mode} (指定ID: {self.custom_provider_id or '无'})\n"
            f"回复风格: {self.reply_style}\n"
            f"回复长度: {self.reply_length_mode} (上限: {self.max_chars}字)\n"
            f"置信度门槛: {self.min_confidence}\n"
            f"回复概率: {self.reply_probability}%\n"
            f"会话冷却: {self.session_cooldown}秒 | 用户冷却: {self.user_cooldown}秒\n"
            f"最大连续回复: {self.max_continuous_replies}轮\n"
            f"活跃回复类型: {active_types_str or '全部关闭'}\n"
            f"黑白名单模式: {self.filter_mode}\n"
            "==================================="
        )
        yield event.plain_result(status_text)

    @filter.command("typesafe_test")
    async def command_typesafe_test(self, event: AstrMessageEvent, message: str = ""):
        """测试 TypeSafe AI 对指定消息的意图分类判断 (不触发真实回复)"""
        # 优先提取整条消息文本中去掉指令前缀后的完整多词内容
        raw_msg = event.get_message_str() or ""
        match = re.search(r"/?typesafe_test\s+(.*)", raw_msg, re.DOTALL | re.IGNORECASE)
        if match:
            test_text = match.group(1).strip()
        else:
            test_text = (message or "").strip()

        if not test_text:
            yield event.plain_result("请在指令后输入要测试的消息内容，例如：/typesafe_test 怎么安装 Docker？")
            return

        if not self.typesafe_client.is_configured():
            yield event.plain_result("错误：TypeSafe API Key 尚未配置，无法执行测试。")
            return

        yield event.plain_result(f"正在使用 TypeSafe AI ({self.typesafe_model}) 分析消息：\n“{test_text}”...")

        state = {
            "recent_chat": [],
            "current_message": {
                "sender": "测试用户",
                "text": test_text,
            },
        }

        decision = await self.classifier.classify_message(state)

        result_text = (
            "=== TypeSafe AI 结构化判断结果 ===\n"
            f"测试消息: {test_text}\n"
            f"判定模型: {self.typesafe_model}\n"
            f"是否建议回复: {'【是】' if decision.should_reply else '【否】'}\n"
            f"意图分类: {decision.reply_type_display} ({decision.reply_type})\n"
            f"置信度等级: {decision.confidence_level} ({decision.confidence_score:.2f})\n"
            f"紧迫程度: {decision.urgency}\n"
            f"内部理由: {decision.reason}\n"
            f"是否故障降级: {'是' if decision.is_fallback else '否'}\n"
            "=================================="
        )
        yield event.plain_result(result_text)

    # ═══════════════════════════════════════════════════════════
    # WebUI 配置中心（Dashboard 插件页）
    # ═══════════════════════════════════════════════════════════

    def _plugin_data_dir(self):
        """AstrBot 标准插件数据目录：data/plugin_data/<plugin_name>/。"""
        from pathlib import Path

        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            base = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_typesafe_autoreply"
        except Exception:
            base = Path("data") / "plugin_data" / "astrbot_plugin_typesafe_autoreply"
        try:
            base.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"[TypeSafe] 创建插件数据目录失败 {base}: {e}")
        return base

    def _register_web_apis(self):
        """注册配置中心所需的全部 Web API 路由。

        页面通过 window.AstrBotPluginPage.apiGet("config/get") 调用，
        Dashboard 会转发到 /api/plug/astrbot_plugin_typesafe_autoreply/config/get。
        """
        plugin_name = "astrbot_plugin_typesafe_autoreply"  # 与 metadata.yaml 的 name 一致
        routes = (
            ("console/config", self._api_config_get, ["GET"], "读取插件配置（密钥已掩码）"),
            ("console/config/update", self._api_config_update, ["POST"], "更新插件配置并重载"),
            ("console/config/reset", self._api_config_reset, ["POST"], "将选中字段恢复为默认值"),
            ("console/status", self._api_status, ["GET"], "读取运行状态与生效规则"),
            ("console/try", self._api_try, ["POST"], "在线试判一条消息"),
            ("console/probe", self._api_probe, ["POST"], "测试 TypeSafe API 连通性"),
            ("console/qa/list", self._api_qa_list, ["GET"], "读取固定问答表"),
            ("console/qa/save", self._api_qa_save, ["POST"], "保存某个作用域的问答表"),
            ("console/qa/import", self._api_qa_import, ["POST"], "从 KeyReply 数据文件导入"),
            ("console/qa/test", self._api_qa_test, ["POST"], "测试一条消息的命中结果"),
        )
        for path, handler, methods, desc in routes:
            try:
                self.context.register_web_api(
                    f"/{plugin_name}/{path}", handler, methods, desc
                )
            except Exception as e:  # 老版本 AstrBot 不支持插件页时不影响插件主体
                logger.warning(f"[TypeSafe] 注册 Web API {path} 失败: {e}")

    def _plugin_config_object(self):
        """拿到 AstrBot 持有的 AstrBotConfig（非副本），拿不到时退回实例配置。"""
        try:
            from astrbot.core.star.star import star_registry

            for plugin_md in star_registry:
                if plugin_md.name == "astrbot_plugin_typesafe_autoreply":
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
            logger.warning(f"[TypeSafe] 配置保存失败: {e}")
            return False

        try:
            if hasattr(self.context, "reload_plugin"):
                await self.context.reload_plugin("astrbot_plugin_typesafe_autoreply")
                return True
            if hasattr(self.context, "_star_manager"):
                await self.context._star_manager.reload("astrbot_plugin_typesafe_autoreply")
                return True
        except Exception as e:
            logger.warning(f"[TypeSafe] 插件重载失败: {e}")
            return False

        logger.warning("[TypeSafe] 找不到 reload 方法，配置已保存但需手动重载插件")
        return False

    def _audit_config_summary(self) -> dict:
        """供页面展示的配置摘要（不含任何密钥明文）。"""
        return {
            "enable_plugin": bool(self.enable_plugin),
            "enable_group": bool(self.enable_group),
            "enable_private": bool(self.enable_private),
            "configured": self.typesafe_client.is_configured(),
            "model": self.typesafe_model,
            "reply_style": self.reply_style,
            "reply_length_mode": self.reply_length_mode,
            "min_confidence": self.min_confidence,
            "reply_source": self.reply_source,
            "qa_mode": self.qa_mode,
            "allowed_reply_types": list(self.allowed_reply_types),
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
            logger.warning(f"[TypeSafe] 读取 _conf_schema.json 失败: {e}")
            return {}
        return {
            key: item.get("default")
            for key, item in schema.items()
            if isinstance(item, dict) and "default" in item
        }

    async def _api_status(self) -> dict:
        """运行状态快照：等价于 /typesafe_status 指令的结构化版本。"""
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
            "version": "1.0.5",
            "enable_plugin": bool(self.enable_plugin),
            "enable_group": bool(self.enable_group),
            "enable_private": bool(self.enable_private),
            "api_configured": self.typesafe_client.is_configured(),
            "model": self.typesafe_model,
            "model_raw": self.typesafe_model_raw,
            "custom_model": self.typesafe_custom_model,
            "timeout": self.typesafe_client.timeout,
            "failure_mode": self.failure_mode,
            "min_confidence": self.min_confidence,
            "reply_probability": self.reply_probability,
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
            "allowed_reply_types": [
                REPLY_TYPE_NAMES.get(t, t) for t in self.allowed_reply_types
            ],
            "context_message_count": self.context_message_count,
            "rate_limit_per_minute": getattr(
                self.typesafe_client.rate_limiter, "limit_per_minute", 0
            ),
            "rate_limit_used": self.typesafe_client.rate_limiter.current_load(),
            "cache_enabled": bool(self.enable_cache),
            "cache_ttl": self.cache_ttl,
            "cache_size": len(getattr(self.typesafe_client.cache, "_cache", {}))
            if getattr(self.typesafe_client, "cache", None)
            else 0,
            "debug_log": bool(self.debug_log),
            "active_sessions": active,
            "force_reply_mode": self.force_reply_mode,
            "force_trigger_regex": self.force_trigger_regex,
            "ignore_regex": self.ignore_regex,
            "qa_mode": self.qa_mode,
            "qa_mode_label": MODE_LABELS.get(self.qa_mode, self.qa_mode),
            "use_qa_table": bool(self.use_qa_table),
            "qa_enable_jev_topic": bool(self.enable_jev_topic),
            "qa_min_confidence": self.qa_min_confidence,
            "qa_fallback_to_llm": bool(self.qa_fallback_to_llm),
            "qa_summary": self.qa_store.scope_summary(),
            "regex_ok": {
                "force_trigger": bool(self.force_trigger_regex_pattern)
                or not (self.force_trigger_regex or "").strip(),
                "ignore": bool(self.ignore_regex_pattern)
                or not (self.ignore_regex or "").strip(),
            },
        }

    async def _api_try(self):
        """在线试判：等价于 /typesafe_test 指令的 JSON 版本，不触发真实回复。"""
        from quart import request

        payload = await request.get_json(silent=True) or {}
        text = str(payload.get("text") or "").strip()
        if not text:
            return {"ok": False, "message": "请输入要试判的消息内容"}, 400

        if not self.typesafe_client.is_configured():
            return {"ok": False, "message": "TypeSafe API Key 尚未配置，无法试判"}, 400

        # 可选：附带最近 N 条模拟上下文，验证上下文对判定的影响
        recent: list[dict] = []
        raw_recent = payload.get("recent")
        if isinstance(raw_recent, list):
            for item in raw_recent[:10]:
                if isinstance(item, dict) and str(item.get("text") or "").strip():
                    recent.append(
                        {
                            "sender": str(item.get("sender") or "群友").strip(),
                            "text": str(item.get("text")).strip(),
                        }
                    )

        sender = str(payload.get("sender") or "测试用户").strip() or "测试用户"
        state = {
            "recent_chat": recent,
            "current_message": {"sender": sender, "text": text},
        }

        started = time.perf_counter()
        try:
            decision = await self.classifier.classify_message(state)
        except Exception as e:
            logger.error(f"[TypeSafe] 在线试判失败: {e}", exc_info=True)
            return {"ok": False, "message": f"试判失败: {e}"}, 500
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        allowed = MessageClassifier.is_reply_type_allowed(
            decision.reply_type, self.allowed_reply_types
        )
        confidence_ok = MessageClassifier.is_confidence_sufficient(
            decision.confidence_level, self.min_confidence
        )
        type_display = decision.reply_type_display

        reasons: list[str] = []
        if decision.is_fallback:
            reasons.append("TypeSafe 判定失败，已按降级策略给出兜底结论")
        if not decision.should_reply:
            reasons.append("TypeSafe 判定无需主动回复")
        else:
            if not allowed:
                reasons.append(
                    f"意图「{type_display}」未包含在允许回复列表中，实际会被静默"
                )
            if not confidence_ok:
                reasons.append(
                    f"置信度「{decision.confidence_level}」未达到阈值「{self.min_confidence}」"
                )
        would_reply = bool(
            decision.should_reply
            and allowed
            and confidence_ok
            and not decision.is_fallback
        )

        return {
            "ok": True,
            "text": text,
            "model": self.typesafe_model,
            "elapsed_ms": round(elapsed_ms, 1),
            "should_reply": decision.should_reply,
            "reply_type": decision.reply_type,
            "reply_type_display": type_display,
            "confidence_level": decision.confidence_level,
            "confidence_score": round(float(decision.confidence_score), 4),
            "urgency": decision.urgency,
            "reason": decision.reason,
            "is_fallback": bool(decision.is_fallback),
            "type_allowed": bool(allowed),
            "confidence_ok": bool(confidence_ok),
            "would_reply": would_reply,
            "verdicts": reasons,
            "gates": {
                "allowed_types": [
                    REPLY_TYPE_NAMES.get(t, t) for t in self.allowed_reply_types
                ],
                "min_confidence": self.min_confidence,
                "reply_probability": self.reply_probability,
                "context_message_count": self.context_message_count,
            },
        }

    async def _api_probe(self):
        """一键测试 TypeSafe API 连通性。"""
        if not self.typesafe_client:
            return {"ok": False, "message": "客户端未初始化"}, 500

        started = time.perf_counter()
        result = await self.typesafe_client.test_api_connection()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return {
            "ok": bool(result.get("ok")),
            "message": result.get("message", ""),
            "model": self.typesafe_model,
            "raw_model": getattr(result.get("data"), "model", None),
            "elapsed_ms": round(elapsed_ms, 1),
            "rate_limit_used": self.typesafe_client.rate_limiter.current_load(),
        }

    # ── 固定问答表 API ────────────────────────────────────
    async def _api_qa_list(self) -> dict:
        """返回全部问答表、作用域摘要与导入候选路径。"""
        tables = []
        for t in self.qa_store.all_tables():
            tables.append({
                "scope": t.scope,
                "scope_id": t.scope_id,
                "key": t.key,
                "label": t.label,
                "entries": t.entries,
            })
        return {
            "tables": tables,
            "summary": self.qa_store.scope_summary(),
            "data_file": str(self.qa_store.path),
            "import_candidates": self.qa_store.find_keyreply_files(),
            "configured_import_path": self.qa_import_path,
            "mode": self.qa_mode,
            "mode_label": MODE_LABELS.get(self.qa_mode, self.qa_mode),
            "enable_jev_topic": self.enable_jev_topic,
            "use_qa_table": self.use_qa_table,
            "qa_min_confidence": self.qa_min_confidence,
            "qa_fallback_to_llm": self.qa_fallback_to_llm,
            "context_message_count": self.context_message_count,
        }

    async def _api_qa_save(self):
        """保存某个作用域的问答表（整体替换）。"""
        from quart import request

        payload = await request.get_json(silent=True) or {}
        scope = str(payload.get("scope") or SCOPE_GLOBAL)
        scope_id = str(payload.get("scope_id") or "").strip()
        entries = payload.get("entries")

        if scope not in (SCOPE_GLOBAL, SCOPE_GROUP, SCOPE_PRIVATE):
            return {"message": f"未知作用域: {scope}"}, 400
        if scope in (SCOPE_GROUP, SCOPE_PRIVATE) and not scope_id:
            return {"message": "群聊/私聊专属表必须提供 scope_id"}, 400
        if not isinstance(entries, list):
            return {"message": "entries 必须是数组"}, 400

        table = self.qa_store.replace_table(scope, scope_id, entries)
        # 保存时按答案指纹重新分组，使「多个 Q 指向同一个 A」在页面上可直接体现
        regroup_answers(table)
        if not self.qa_store.save():
            return {"message": "写入数据文件失败，请检查目录权限"}, 500

        logger.info(
            f"[TypeSafe][QA] 已保存问答表 {table.label}，共 {len(table.entries)} 条"
        )
        return {
            "message": "ok",
            "table": {"scope": table.scope, "scope_id": table.scope_id, "entries": table.entries},
            "summary": self.qa_store.scope_summary(),
        }

    async def _api_qa_import(self):
        """从 KeyReply 的 triggers.yml 导入问答对。"""
        from quart import request

        payload = await request.get_json(silent=True) or {}
        scope = str(payload.get("scope") or SCOPE_GLOBAL)
        scope_id = str(payload.get("scope_id") or "").strip()
        path_text = str(payload.get("path") or self.qa_import_path or "").strip()

        if scope not in (SCOPE_GLOBAL, SCOPE_GROUP, SCOPE_PRIVATE):
            return {"message": f"未知作用域: {scope}"}, 400
        if scope in (SCOPE_GROUP, SCOPE_PRIVATE) and not scope_id:
            return {"message": "群聊/私聊专属表必须提供 scope_id"}, 400

        from pathlib import Path

        if not path_text:
            candidates = self.qa_store.find_keyreply_files()
            if not candidates:
                return {
                    "ok": False,
                    "message": "未找到 KeyReply 数据文件，请在设置中手动填写 triggers.yml 的完整路径",
                }, 404
            path_text = candidates[0]

        result = self.qa_store.import_keyreply_file(Path(path_text), scope, scope_id)
        status = 200 if result.get("ok") else 400
        result["path"] = path_text
        result["summary"] = self.qa_store.scope_summary()
        return result, status

    async def _api_qa_test(self):
        """测试一条消息在当前配置下的命中结果（不发送任何消息）。"""
        from quart import request

        payload = await request.get_json(silent=True) or {}
        text = str(payload.get("text") or "").strip()
        if not text:
            return {"ok": False, "message": "请输入要测试的消息内容"}, 400

        scope = str(payload.get("scope") or SCOPE_GLOBAL)
        scope_id = str(payload.get("scope_id") or "").strip()
        if scope not in (SCOPE_GLOBAL, SCOPE_GROUP, SCOPE_PRIVATE):
            return {"message": f"未知作用域: {scope}"}, 400

        # 经典模式：直接看正则命中
        classic = self.qa_store.find_reply(text, scope, scope_id)
        classic_view = None
        if classic:
            entry = classic.get("entry") or {}
            classic_view = {
                "question": question_of(entry),
                "answer": answer_text_of(entry),
                "images": answer_images_of(entry),
                "table_label": classic.get("table_label"),
            }

        # Jev 模式：真实调用一次话题判断
        jev_view = None
        questions = self.qa_store.candidate_questions(scope, scope_id)
        if self.typesafe_client.is_configured() and questions:
            state = {
                "recent_chat": [],
                "current_message": {"sender": "测试用户", "text": text},
            }
            started = time.perf_counter()
            topic = await self.classifier.match_topic(state=state, questions=questions)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            grounded = None
            if topic.matched:
                m = self.qa_store.find_reply_by_question(topic.question, scope, scope_id)
                if m:
                    grounded = answer_text_of(m.get("entry"))
            jev_view = {
                "matched": topic.matched,
                "question": topic.question,
                "confidence_level": topic.confidence_level,
                "confidence_score": round(float(topic.confidence_score), 4),
                "reason": topic.reason,
                "is_fallback": topic.is_fallback,
                "answer": grounded,
                "confidence_ok": MessageClassifier.is_confidence_sufficient(
                    topic.confidence_level, self.qa_min_confidence
                ),
                "elapsed_ms": round(elapsed_ms, 1),
            }
        elif not questions:
            jev_view = {"matched": False, "reason": "问答表为空，无可判断的话题"}
        else:
            jev_view = {"matched": False, "reason": "TypeSafe API Key 未配置，无法进行话题判断"}

        return {
            "ok": True,
            "text": text,
            "scope": scope,
            "scope_id": scope_id,
            "mode": self.qa_mode,
            "mode_label": MODE_LABELS.get(self.qa_mode, self.qa_mode),
            "candidate_count": len(questions),
            "classic": classic_view,
            "jev": jev_view,
            "would_reply": bool(jev_view and jev_view.get("matched") and jev_view.get("confidence_ok"))
            if self.qa_mode == MODE_JEV
            else bool(classic_view),
        }

    # ── 固定问答表 API END ────────────────────────────────

    async def terminate(self):
        """插件卸载或停用时的资源释放"""
        logger.info("[TypeSafe] 插件正在卸载，清理资源...")
        if self.typesafe_client.cache:
            self.typesafe_client.cache.clear()
        logger.info("[TypeSafe] 插件卸载完成")
