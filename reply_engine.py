import logging
from typing import Optional

from astrbot.api.star import Context
from astrbot.api.event import AstrMessageEvent

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

logger = logging.getLogger("astrbot")

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


class ReplyEngine:
    """负责构造提示词、调用 AstrBot 已配置的 LLM Provider 并执行长度安全保护"""

    def __init__(self, context: Context):
        self.context = context

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
        """
        组装提示词并调用 AstrBot LLM 生成文本回复
        """
        # 1. 确定使用的 Provider ID
        provider_id: Optional[str] = None
        norm_model_mode = "custom" if ("指定" in str(model_mode) or "custom" in str(model_mode)) else "follow_session"

        if norm_model_mode == "custom" and custom_provider_id.strip():
            provider_id = custom_provider_id.strip()
        else:
            try:
                provider_id = await self.context.get_current_chat_provider_id(
                    umo=event.unified_msg_origin
                )
            except Exception as e:
                logger.warning(f"[ReplyEngine] 获取当前会话 Provider ID 失败: {e}")

        # 如果未获取到会话 Provider ID，尝试 fallback 到 AstrBot 默认生效的 Provider
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
            logger.error("[ReplyEngine] 未能获取到有效的 LLM Provider ID，请在 AstrBot 设置中配置默认服务提供商，或在插件中指定 LLM Provider ID")
            return None

        # 2. 组装提示词 (归一化风格与长度模式)
        canonical_style = normalize_reply_style(reply_style)
        canonical_length = normalize_reply_length_mode(reply_length_mode)

        style_desc = STYLE_DESCRIPTIONS.get(canonical_style, STYLE_DESCRIPTIONS["natural"])
        length_desc = LENGTH_DESCRIPTIONS.get(canonical_length, LENGTH_DESCRIPTIONS["short"])

        system_instruction = (
            "你正在参与一个真实的群聊。\n"
            "你的任务是作为群内成员，针对用户的发言或提问给出自然、真实、有帮助的回复。\n"
            "绝对要求：\n"
            "- 直接给出回答内容，严禁输出任何关于判断、分类、决策或前缀的说明。\n"
            "- 严禁使用'根据你的问题'、'作为人工智能'、'您好，很高兴为您解答'等机械化AI套话。\n"
            "- 除非被直接询问，否则不要主动宣称自己是 AI。\n"
            "- 回复必须像真实群成员发出的消息一样自然融入群聊氛围。\n"
        )

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
            user_content += f"- 图片说明：用户发送了 {len(image_urls)} 张图片，请仔细查看图片内容并结合上下文给出有针对性的回复。\n"

        user_content += "\n请直接回复该条消息："

        img_log = f" (附带 {len(image_urls)} 张图片)" if image_urls else ""
        logger.debug(f"[ReplyEngine] 正在调用 Provider: {provider_id} 生成回复{img_log}...")

        # 3. 调用 AstrBot LLM
        try:
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=user_content,
                system_prompt=system_instruction,
                image_urls=image_urls if image_urls else None,
            )

            if not llm_resp:
                logger.warning("[ReplyEngine] LLM 生成返回空结果")
                return None

            raw_text = getattr(llm_resp, "completion_text", "")
            if not raw_text:
                logger.warning("[ReplyEngine] completion_text 为空")
                return None

            raw_text = raw_text.strip()
            # 4. 长度保护截断
            protected_text = truncate_reply(raw_text, max_chars)
            logger.info(
                f"[ReplyEngine] 回复生成成功 (原始字数: {len(raw_text)}, 保护截断后: {len(protected_text)})"
            )
            return protected_text

        except Exception as e:
            logger.error(f"[ReplyEngine] 调用 AstrBot LLM 发生异常: {e}", exc_info=True)
            return None
