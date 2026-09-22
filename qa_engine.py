# -*- coding: utf-8 -*-
"""固定问答表（KeyReply）的回复分派与提示词构造。

两种模式，由 enable_jev_topic 开关整体切换：

- **经典模式（开关关闭）**：严格等同 KeyReply 原行为。
  问题中的 % 是通配符，直接对消息做正则匹配，命中即原样发送答案（含图片）。
- **Jev 话题模式（开关开启）**：把 QA 表的 question 列表作为「话题判断关键词」
  交给 Jev 模型做语义路由；命中后取该条的答案 A，连同与当前消息相同数量的
  聊天上下文交给 LLM，围绕 A 生成自然回复。A 是唯一事实来源。

两种模式下，未命中任何条目都保持静默。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


MODE_CLASSIC = "classic"
MODE_JEV = "jev"

MODE_LABELS = {
    MODE_CLASSIC: "经典模式（正则匹配，直接发送固定答案）",
    MODE_JEV: "Jev 话题模式（语义路由，LLM 围绕答案生成）",
}


def resolve_mode(enable_jev_topic: bool) -> str:
    return MODE_JEV if enable_jev_topic else MODE_CLASSIC


def build_grounded_prompt(
    current_message: str,
    answer_text: str,
    chat_context: str,
    style_desc: str,
    length_desc: str,
    max_chars: int = 200,
    custom_prompt: str = "",
    image_count: int = 0,
) -> Tuple[str, str]:
    """构造「围绕固定答案 A 生成」的提示词，返回 (system_prompt, user_content)。

    约束强度：严格以 A 为准，仅做措辞润色与上下文衔接。
    """
    system_instruction = (
        "你正在参与一个真实的群聊，角色是群内成员。\n"
        "本次回复有且只有一个已经确定好的标准答案，你的任务是用自然的口语把它表达出来。\n"
        "绝对要求：\n"
        "- 标准答案是本次回复的唯一事实来源，严禁新增、修改、推测或补充任何事实内容。\n"
        "- 严禁改变标准答案的含义、结论、数字、名称、链接等关键信息。\n"
        "- 你只能调整措辞、语气与上下文衔接，让回复像真实群成员随口说出的话。\n"
        "- 严禁输出「根据标准答案」「答案是」「作为AI」这类暴露机制的说明。\n"
        "- 如果标准答案本身已经足够口语化，直接原样输出即可，不要画蛇添足。\n"
        "- 超出标准答案范围的内容一律不要提及，也不要用「我不确定」之类的话搪塞。"
    )

    user_content = (
        f"【最近聊天上下文】：\n{chat_context or '（无）'}\n\n"
        f"【用户当前消息】：\n{current_message}\n\n"
        f"【标准答案（唯一事实来源）】：\n{answer_text}\n\n"
        f"【回复要求】：\n"
        f"- 围绕上面的标准答案作答，不得偏离其内容；\n"
        f"- 风格要求：{style_desc}\n"
        f"- 篇幅要求：{length_desc}\n"
        f"- 最长不超过 {max_chars} 字。\n"
    )

    if image_count:
        user_content += f"- 用户随消息发送了 {image_count} 张图片，可结合语境回应。\n"
    if custom_prompt and custom_prompt.strip():
        user_content += f"- 附加要求：{custom_prompt.strip()}\n"

    user_content += "\n请直接输出这条群聊回复："
    return system_instruction, user_content


def answer_text_of(entry: Optional[Dict[str, Any]]) -> str:
    """取出问答对的答案文本。"""
    if not entry:
        return ""
    answer = entry.get("answer") or {}
    return str(answer.get("text") or "")


def answer_images_of(entry: Optional[Dict[str, Any]]) -> List[str]:
    """取出问答对的答案图片 URL 列表。"""
    if not entry:
        return []
    answer = entry.get("answer") or {}
    images = answer.get("images") or []
    return [str(x).strip() for x in images if str(x).strip()]


def resolved_text(match: Optional[Dict[str, Any]]) -> str:
    """取出一次命中最终生效的答案文本（已考虑 answer_key → 答案池）。"""
    if not match:
        return ""
    ans = match.get("answer")
    if isinstance(ans, dict):
        return str(ans.get("text") or "")
    return answer_text_of(match.get("entry"))


def resolved_images(match: Optional[Dict[str, Any]]) -> List[str]:
    """取出一次命中最终生效的答案图片（已考虑 answer_key → 答案池）。"""
    if not match:
        return []
    ans = match.get("answer")
    if isinstance(ans, dict):
        images = ans.get("images") or []
    else:
        images = answer_images_of(match.get("entry"))
    return [str(x).strip() for x in images if str(x).strip()]


def question_of(entry: Optional[Dict[str, Any]]) -> str:
    if not entry:
        return ""
    return str(entry.get("question") or "")


def describe_match(match: Optional[Dict[str, Any]]) -> str:
    """把匹配结果格式化为一行日志描述。"""
    if not match:
        return "未命中任何固定问答"
    entry = match.get("entry") or {}
    return (
        f"命中「{question_of(entry)}」"
        f" (来源: {match.get('table_label') or match.get('table_key') or '未知'})"
    )


def build_scope(scope_type: str, scope_id: str) -> str:
    """把会话信息归一化为 QAStore 的作用域参数。"""
    if scope_type == "group" and scope_id:
        return "group", str(scope_id)
    if scope_type == "private" and scope_id:
        return "private", str(scope_id)
    return "global", ""
