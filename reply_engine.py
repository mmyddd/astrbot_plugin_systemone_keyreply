from typing import Optional

from astrbot.api.star import Context
from astrbot.api.event import AstrMessageEvent
from astrbot.api import logger

import sys
from pathlib import Path

plugin_dir = str(Path(__file__).parent.resolve())
if plugin_dir not in sys.path:
    sys.path.insert(0, plugin_dir)

try:
    from .utils import (
        truncate_reply,
        normalize_reply_style,
        normalize_reply_length_mode,
    )
except (ImportError, ValueError):
    from utils import (
        truncate_reply,
        normalize_reply_style,
        normalize_reply_length_mode,
    )


STYLE_DESCRIPTIONS = {
    "natural": "像普通群成员一样自然交流，语言生活化，不使用机械、套话或过于正式的公文语气。",
    "concise": "极致简洁，直奔主题，只回答核心关键点，不做无关背景展开。",
    "professional": "专业严谨，用词规范准确，表达清晰，逻辑连贯。",
    "friendly": "热情友好，亲和力强，富有耐心。",
    "humorous": "幽默风趣，轻松愉快，自然融入群聊融洽氛围。",
    "lively": "活泼跳跃，充满朝气，语调轻松灵动。",
    "rigorous": "严谨求实，注重技术准确性，条理清晰。",
    "group_peer": "地道的群友风格，降低AI味，接地气，像日常群成员随口交流一样，绝不长篇大论说教。",
    "custom": "自然得体地交流。",
}

LENGTH_DESCRIPTIONS = {
    "very_short": "极短（约20~50字），一针见血，绝不冗长。",
    "short": "简短（约50~100字），言简意赅，说明核心要点。",
    "normal": "正常（约100~250字），条理清楚，解答完整。",
    "detailed": "详细（约250~500字），分点详尽拆解说明。",
    "custom": "根据问题复杂度自然控制篇幅，尽量精炼。",
}

REPLY_SYSTEM_INSTRUCTION = (
    "你正在参与一个真实的群聊。\n"
    "你的任务是作为群内成员，针对用户的发言或提问给出自然、真实、有帮助的回复。\n"
    "绝对要求：\n"
    "- 直接给出回答内容，严禁输出任何关于判断、分类、决策或前缀的说明。\n"
    "- 严禁使用'根据你的问题'、'作为人工智能'、'您好，很高兴为您解答'等机械化AI套话。\n"
    "- 除非被直接询问，否则不要主动宣称自己是 AI。\n"
    "- 回复必须像真实群成员发出的消息一样自然融入群聊氛围。\n"
)


class ReplyEngine:
    """负责构造提示词、调用 AstrBot 已配置的 LLM Provider 并执行长度安全保护"""

    def __init__(self, context: Context):
        self.context = context

    # ── Provider 解析 ─────────────────────────────────────
    async def resolve_provider_id(
        self,
        event: AstrMessageEvent,
        model_mode: str = "follow_session",
        custom_provider_id: str = "",
    ) -> Optional[str]:
        """解析本次生成应使用的 Provider ID（指定 Provider / 会话模型 / 全局兜底）。"""
        provider_id: Optional[str] = None
        norm_model_mode = "custom" if (
            "指定" in str(model_mode) or "custom" in str(model_mode)
        ) else "follow_session"

        if norm_model_mode == "custom" and custom_provider_id.strip():
            provider_id = custom_provider_id.strip()
        else:
            try:
                provider_id = await self.context.get_current_chat_provider_id(
                    umo=event.unified_msg_origin
                )
            except Exception as e:
                logger.warning(f"[ReplyEngine] 获取当前会话 Provider ID 失败: {e}")

        # 未获取到会话 Provider ID 时，回退到 AstrBot 默认生效的 Provider
        if not provider_id:
            try:
                if hasattr(self.context, "get_using_provider_async"):
                    default_p = await self.context.get_using_provider_async(event.unified_msg_origin)
                    if default_p:
                        provider_id = getattr(default_p, "id", None) or getattr(default_p, "name", None)
                elif hasattr(self.context, "get_using_provider"):
                    default_p = self.context.get_using_provider(event.unified_msg_origin)
                    if default_p:
                        provider_id = getattr(default_p, "id", None) or getattr(default_p, "name", None)
            except Exception as e:
                logger.debug(f"[ReplyEngine] 获取默认 Provider 失败: {e}")

        if not provider_id:
            logger.error(
                "[ReplyEngine] 未能获取到有效的 LLM Provider ID，请在 AstrBot 设置中配置默认服务提供商，"
                "或在插件中指定 LLM Provider ID"
            )
            return None
        return provider_id

    # ── 调用底层 LLM ──────────────────────────────────────
    async def _invoke_llm(
        self,
        event: AstrMessageEvent,
        provider_id: str,
        system_instruction: str,
        user_content: str,
        image_urls: Optional[list],
        max_chars: int,
        tag: str = "ReplyEngine",
    ) -> Optional[str]:
        """统一的底层调用、空值校验与长度保护。"""
        img_log = f" (附带 {len(image_urls)} 张图片)" if image_urls else ""
        logger.debug(f"[{tag}] 正在调用 Provider: {provider_id} 生成回复{img_log}...")
        try:
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=user_content,
                system_prompt=system_instruction,
                image_urls=image_urls if image_urls else None,
            )

            if not llm_resp:
                logger.warning(f"[{tag}] LLM 生成返回空结果")
                return None

            raw_text = getattr(llm_resp, "completion_text", "")
            if not raw_text:
                logger.warning(f"[{tag}] completion_text 为空")
                return None

            raw_text = raw_text.strip()
            protected_text = truncate_reply(raw_text, max_chars)
            logger.info(
                f"[{tag}] 回复生成成功 (原始字数: {len(raw_text)}, 保护截断后: {len(protected_text)})"
            )
            return protected_text

        except Exception as e:
            logger.error(f"[{tag}] 调用 AstrBot LLM 发生异常: {e}", exc_info=True)
            return None

    # ── 自由生成（原有行为）────────────────────────────────
    async def generate_reply(
        self,
        event: AstrMessageEvent,
        current_message: str,
        chat_context: str,
        model_mode: str = "follow_session",
        custom_provider_id: str = "",
        reply_style: str = "natural",
        reply_length_mode: str = "short",
        max_chars: int = 200,
        custom_prompt: str = "",
        image_urls: Optional[list] = None,
    ) -> Optional[str]:
        """组装提示词并调用 AstrBot LLM 生成自由文本回复。"""
        provider_id = await self.resolve_provider_id(event, model_mode, custom_provider_id)
        if not provider_id:
            return None

        canonical_style = normalize_reply_style(reply_style)
        canonical_length = normalize_reply_length_mode(reply_length_mode)
        style_desc = STYLE_DESCRIPTIONS.get(canonical_style, STYLE_DESCRIPTIONS["natural"])
        length_desc = LENGTH_DESCRIPTIONS.get(canonical_length, LENGTH_DESCRIPTIONS["short"])

        user_content = (
            f"【最近聊天上下文】：\n{chat_context}\n\n"
            f"【当前用户消息】：\n{current_message}\n\n"
            f"【回复要求】：\n"
            f"- 风格要求：{style_desc}\n"
            f"- 篇幅要求：{length_desc}\n"
        )

        if custom_prompt and custom_prompt.strip():
            user_content += f"- 附加要求：{custom_prompt.strip()}\n"

        if image_urls:
            user_content += (
                f"- 图片说明：用户发送了 {len(image_urls)} 张图片，"
                "请仔细查看图片内容并结合上下文给出有针对性的回复。\n"
            )

        user_content += "\n请直接回复该条消息："

        return await self._invoke_llm(
            event=event,
            provider_id=provider_id,
            system_instruction=REPLY_SYSTEM_INSTRUCTION,
            user_content=user_content,
            image_urls=image_urls,
            max_chars=max_chars,
            tag="ReplyEngine",
        )

    # ── 围绕固定答案生成（QA 模式）──────────────────────────
    async def generate_grounded_reply(
        self,
        event: AstrMessageEvent,
        current_message: str,
        chat_context: str,
        grounded_answer: str,
        model_mode: str = "follow_session",
        custom_provider_id: str = "",
        reply_style: str = "natural",
        reply_length_mode: str = "short",
        max_chars: int = 200,
        custom_prompt: str = "",
        image_urls: Optional[list] = None,
    ) -> Optional[str]:
        """固定问答表模式：围绕给定的标准答案生成自然回复。

        与 generate_reply 的唯一区别是提示词——这里把 grounded_answer 作为
        唯一事实来源注入，约束 LLM 不得偏离其内容。
        """
        provider_id = await self.resolve_provider_id(event, model_mode, custom_provider_id)
        if not provider_id:
            return None

        try:
            from .qa_engine import build_grounded_prompt
        except (ImportError, ValueError):
            from qa_engine import build_grounded_prompt

        canonical_style = normalize_reply_style(reply_style)
        canonical_length = normalize_reply_length_mode(reply_length_mode)
        style_desc = STYLE_DESCRIPTIONS.get(canonical_style, STYLE_DESCRIPTIONS["natural"])
        length_desc = LENGTH_DESCRIPTIONS.get(canonical_length, LENGTH_DESCRIPTIONS["short"])

        system_instruction, user_content = build_grounded_prompt(
            current_message=current_message,
            answer_text=grounded_answer,
            chat_context=chat_context,
            style_desc=style_desc,
            length_desc=length_desc,
            max_chars=max_chars,
            custom_prompt=custom_prompt,
            image_count=len(image_urls) if image_urls else 0,
        )

        return await self._invoke_llm(
            event=event,
            provider_id=provider_id,
            system_instruction=system_instruction,
            user_content=user_content,
            image_urls=image_urls,
            max_chars=max_chars,
            tag="ReplyEngine[QA]",
        )
