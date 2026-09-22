from dataclasses import dataclass
from typing import Dict, Any, List, Optional

from typesafe_sdk import Choice
import sys
from pathlib import Path

plugin_dir = str(Path(__file__).parent.resolve())
if plugin_dir not in sys.path:
    sys.path.insert(0, plugin_dir)

try:
    from .typesafe_client import TypeSafeClientWrapper
    from .utils import normalize_failure_mode, normalize_confidence_level
except (ImportError, ValueError):
    from typesafe_client import TypeSafeClientWrapper
    from utils import normalize_failure_mode, normalize_confidence_level


# 回复类型名称映射，供日志和格式化展示
REPLY_TYPE_NAMES = {
    "explicit_question": "明确提问",
    "seek_help": "求助",
    "technical_issue": "技术问题",
    "info_query": "信息查询",
    "recommendation": "建议请求",
    "discussion": "讨论",
    "casual_chat": "闲聊",
    "emotion": "情绪表达",
    "joke": "玩笑",
    "statement": "陈述",
    "greeting": "打招呼",
    "thanks": "感谢",
    "farewell": "告别",
    "other": "其他",
}

# 中文名称映射回英文键名
REPLY_NAME_TO_KEY = {v: k for k, v in REPLY_TYPE_NAMES.items()}


@dataclass
class ClassificationDecision:
    should_reply: bool
    reply_type: str
    confidence_level: str  # high, medium, low
    confidence_score: float
    reason: str
    urgency: str  # high, normal, low
    is_fallback: bool = False

    @property
    def reply_type_display(self) -> str:
        return REPLY_TYPE_NAMES.get(self.reply_type, self.reply_type)


@dataclass
class TopicMatch:
    """Jev 相关性判定结果：消息是否真的需要这条固定答案。

    与「话题分类」不同，这里做的是**二分类**——分辨真提问与假命中：
    - 真提问：消息确实在问这件事，应当给出该答案
    - 假命中：消息只是字面上包含 Q 的文字，话题其实无关
    """

    matched: bool                      # True = 真提问，应当回复
    question: str                      # 命中的原始问题文本（未命中为空）
    index: int                         # 命中项在候选列表中的下标，-1 表示未命中
    confidence_score: float
    confidence_level: str
    reason: str
    is_fallback: bool = False
    candidates: Optional[List[str]] = None


class MessageClassifier:
    """负责将消息转换为 TypeSafe System One 提示，并解析结构化裁决结果"""

    def __init__(self, client_wrapper: TypeSafeClientWrapper):
        self.client_wrapper = client_wrapper
        self._questions = self.build_system_one_questions()

    def build_system_one_questions(self) -> Dict[str, Choice]:
        """构造用于消息判断的结构化 Choice 问题集"""
        should_reply_question = Choice(
            instructions=(
                "你负责判断群聊中的当前消息是否适合由 AI 助手主动回复。\n"
                "原则：宁可少回复，也不要乱插话。只有机器人能够明显提供帮助或进行有价值的讨论交流时才主动参与。\n"
                "需主动回复：用户提出明确问题、寻求帮助、询问信息、发起话题讨论（如：你觉得XX怎么样/大家怎么看）、希望获得建议、或发送了图片/截图（如分享图片讨论、发报错截图求助等）。\n"
                "保持静默：群友互相对骂、纯表情包刷屏、简单机械回应（如：收到/1/好的/谢谢）、用户间针对特定人的私聊、或纯陈述无讨论意义的发言。"
            ),
            criteria={
                "yes": "需要主动回复（用户提出了明确提问/求助/信息查询/观点讨论，或发送了图片内容希望互动，且机器人能提供价值）",
                "no": "不需要回复，保持静默（日常表情刷屏、无意义短句、针对其他人的对话或无需回应的发言）",
            },
        )

        reply_type_question = Choice(
            instructions="当前消息最符合以下哪种意图或类型？",
            criteria={
                "explicit_question": "明确提问（如：这个怎么设置？）",
                "seek_help": "求助（如：有人能帮我看看吗？或发送报错截图求助）",
                "technical_issue": "技术问题（如：Docker 为什么启动失败？）",
                "info_query": "信息查询（如：今天有什么更新？）",
                "recommendation": "建议请求（如：大家推荐什么 NAS？）",
                "discussion": "讨论（如：你觉得XX怎么样？大家怎么看？发送图片/内容发起讨论）",
                "casual_chat": "闲聊（如：今天好累啊）",
                "emotion": "情绪表达（如：气死我了）",
                "joke": "玩笑（如：哈哈哈哈）",
                "statement": "陈述（如：我已经处理好了）",
                "greeting": "打招呼（如：早上好）",
                "thanks": "感谢（如：谢谢）",
                "farewell": "告别（如：晚安）",
                "other": "其他消息",
            },
        )

        urgency_question = Choice(
            instructions="用户获得答复的紧迫程度？",
            criteria={
                "high": "紧急（遇到故障报错或急迫求助）",
                "normal": "普通（正常求助与咨询）",
                "low": "低（闲聊或不急切）",
            },
        )

        return {
            "should_reply": should_reply_question,
            "reply_type": reply_type_question,
            "urgency": urgency_question,
        }

    async def classify_message(
        self,
        state: dict,
        cache_key_text: Optional[str] = None,
    ) -> ClassificationDecision:
        """调用 TypeSafe 并解析得出最终裁决对象"""
        questions = self._questions

        api_result = await self.client_wrapper.call_system_one(
            state=state,
            questions=questions,
            cache_key_text=cache_key_text,
        )

        if not api_result or not api_result.get("success"):
            # 处理 API 降级模式 (统一归一化处理中英文降级模式)
            raw_failure_mode = (
                api_result.get("failure_mode") if api_result else self.client_wrapper.failure_mode
            )
            failure_mode = normalize_failure_mode(raw_failure_mode)
            error_reason = api_result.get("error_reason", "unknown") if api_result else "no_result"

            if failure_mode == "pass_to_astrbot":
                return ClassificationDecision(
                    should_reply=True,
                    reply_type="other",
                    confidence_level="medium",
                    confidence_score=0.6,
                    reason=f"TypeSafe API 故障降级(直通 AstrBot): {error_reason}",
                    urgency="normal",
                    is_fallback=True,
                )
            elif failure_mode == "rule_based":
                # 规则备用降级：若是问号结尾则尝试回复
                current_text = ""
                if isinstance(state.get("current_message"), dict):
                    current_text = state["current_message"].get("text", "")
                has_question_mark = "?" in current_text or "？" in current_text
                return ClassificationDecision(
                    should_reply=has_question_mark,
                    reply_type="explicit_question" if has_question_mark else "casual_chat",
                    confidence_level="low",
                    confidence_score=0.5,
                    reason=f"TypeSafe API 故障降级(基础规则判断): {error_reason}",
                    urgency="normal",
                    is_fallback=True,
                )
            else:
                # 默认 silent：静默不回复
                return ClassificationDecision(
                    should_reply=False,
                    reply_type="other",
                    confidence_level="low",
                    confidence_score=0.0,
                    reason=f"TypeSafe API 故障静默降级: {error_reason}",
                    urgency="low",
                    is_fallback=True,
                )

        # 成功拿到 SystemOneResponse
        raw_choices = api_result.get("raw_choices", {})

        # 解析 should_reply
        should_reply_choice = raw_choices.get("should_reply")
        should_reply_val = False
        confidence_score = 0.5
        if should_reply_choice:
            should_reply_val = should_reply_choice.choice == "yes"
            try:
                confidence_score = float(should_reply_choice.confidence)
            except (ValueError, TypeError):
                confidence_score = 0.5

        # 解析 reply_type
        reply_type_choice = raw_choices.get("reply_type")
        reply_type_val = "other"
        if reply_type_choice and reply_type_choice.choice:
            reply_type_val = reply_type_choice.choice

        # 解析 urgency
        urgency_choice = raw_choices.get("urgency")
        urgency_val = "normal"
        if urgency_choice and urgency_choice.choice:
            urgency_val = urgency_choice.choice

        # 置信度等级映射
        confidence_level = self.map_confidence_level(confidence_score)

        reason = (
            f"TypeSafe判断: should_reply={should_reply_val}, "
            f"type={reply_type_val}, confidence={confidence_level}({confidence_score:.2f})"
        )

        return ClassificationDecision(
            should_reply=should_reply_val,
            reply_type=reply_type_val,
            confidence_level=confidence_level,
            confidence_score=confidence_score,
            reason=reason,
            urgency=urgency_val,
            is_fallback=False,
        )

    @staticmethod
    def map_confidence_level(score: float) -> str:
        """置信度数值转等级"""
        if score >= 0.75:
            return "high"
        elif score >= 0.55:
            return "medium"
        else:
            return "low"

    @staticmethod
    def is_confidence_sufficient(level: str, required_min: str) -> bool:
        """判断是否满足最低置信度门槛，支持中英双语输入"""
        rank = {"low": 1, "medium": 2, "high": 3}
        current_norm = normalize_confidence_level(level)
        required_norm = normalize_confidence_level(required_min)
        current_rank = rank.get(current_norm, 1)
        required_rank = rank.get(required_norm, 2)
        return current_rank >= required_rank

    @staticmethod
    def is_reply_type_allowed(reply_type: str, allowed_types: List[str]) -> bool:
        """判断意图类型是否在用户允许的名单内，支持中文名和英文键名双向匹配"""
        if not allowed_types:
            return False

        # 转换为规范英文键名集合与原始集合
        canonical_allowed = set()
        for t in allowed_types:
            t_str = str(t).strip()
            if not t_str:
                continue
            canonical_allowed.add(t_str)
            if t_str in REPLY_NAME_TO_KEY:
                canonical_allowed.add(REPLY_NAME_TO_KEY[t_str])

        return (
            reply_type in canonical_allowed
            or REPLY_TYPE_NAMES.get(reply_type, "") in canonical_allowed
        )

    # ═══════════════════════════════════════════════════════════
    # 固定问答表（KeyReply）话题匹配
    # ═══════════════════════════════════════════════════════════

    MAX_TOPIC_CANDIDATES = 40

    def build_relevance_question(self, candidates: List[Dict[str, Any]]) -> Optional[Choice]:
        """构造「这条消息是否真的需要该回答」的二分类问题。

        criteria 里逐条给出候选的 Q、A 与附加判定增强——Jev 必须同时看到
        问题与答案，才能判断消息是在真的提问，还是只是碰巧包含了 Q 的文字。
        """
        usable = [c for c in (candidates or []) if str(c.get("question") or "").strip()]
        if not usable:
            return None
        usable = usable[: self.MAX_TOPIC_CANDIDATES]

        criteria: Dict[str, str] = {}
        for idx, cand in enumerate(usable):
            q = str(cand.get("question") or "").strip()
            a = str(cand.get("answer_text") or "").strip()
            hint = str(cand.get("hint") or "").strip()
            parts = [f"问题关键词：{q}"]
            if a:
                parts.append(f"对应回答：{a}")
            if hint:
                parts.append(f"补充说明：{hint}")
            criteria[f"relevant{idx}"] = "【应当回答】" + " ｜ ".join(parts)
        criteria["irrelevant"] = (
            "【不应当回答】当前消息并不需要上面任何一个回答"
            "（例如只是字面上包含了关键词，但话题无关；或是与这些话题毫无关系的闲聊）"
        )

        return Choice(
            instructions=(
                "你在判断群聊中的这条消息**是否真的需要**某个已经准备好的固定回答。\n"
                "下面每一项都给出了一个问题关键词、它对应的回答，以及必要的补充说明。\n"
                "判断标准：\n"
                "- 如果用户确实在询问或求助这个回答所覆盖的内容，选择对应的「应当回答」项；\n"
                "- 如果消息只是字面上包含了某个关键词，但实际话题与该回答无关，选择「不应当回答」；\n"
                "- 如果消息与所有条目都无关，同样选择「不应当回答」。\n"
                "请特别注意补充说明中对适用语境的描述，它用于区分容易混淆的情况。\n"
                "宁可选择「不应当回答」，也不要在语境不符时硬套一个答案。"
            ),
            criteria=criteria,
        )

    async def match_relevance(
        self,
        state: dict,
        candidates: List[Dict[str, Any]],
        cache_key_text: Optional[str] = None,
    ) -> TopicMatch:
        """调用 Jev 做二分类：这条消息是否真的需要某个固定回答。

        candidates 由 QAStore.recall_candidates 提供，每条含 question / answer_text / hint。
        失败时一律判为「不相关」，宁可静默也不猜测性回复。
        """
        usable = [c for c in (candidates or []) if str(c.get("question") or "").strip()]
        names = [str(c.get("question") or "") for c in usable]
        if not usable:
            return TopicMatch(
                matched=False, question="", index=-1,
                confidence_score=0.0, confidence_level="low",
                reason="没有候选，无需判定",
                candidates=[],
            )

        question = self.build_relevance_question(usable)
        if question is None:
            return TopicMatch(
                matched=False, question="", index=-1,
                confidence_score=0.0, confidence_level="low",
                reason="候选为空，无法构造判定问题",
                candidates=names,
            )

        api_result = await self.client_wrapper.call_system_one(
            state=state,
            questions={"relevance": question},
            cache_key_text=cache_key_text,
        )

        if not api_result or not api_result.get("success"):
            raw_failure_mode = (
                api_result.get("failure_mode") if api_result else self.client_wrapper.failure_mode
            )
            failure_mode = normalize_failure_mode(raw_failure_mode)
            error_reason = api_result.get("error_reason", "unknown") if api_result else "no_result"
            return TopicMatch(
                matched=False, question="", index=-1,
                confidence_score=0.0, confidence_level="low",
                reason=f"Jev 相关性判定失败({failure_mode}): {error_reason}",
                is_fallback=True,
                candidates=names,
            )

        raw_choices = api_result.get("raw_choices", {})
        rel_choice = raw_choices.get("relevance")
        if not rel_choice or not getattr(rel_choice, "choice", None):
            return TopicMatch(
                matched=False, question="", index=-1,
                confidence_score=0.5, confidence_level="medium",
                reason="Jev 未返回相关性判定结果",
                candidates=names,
            )

        key = str(rel_choice.choice)
        try:
            score = float(rel_choice.confidence)
        except (ValueError, TypeError):
            score = 0.5
        level = self.map_confidence_level(score)

        # 判为「不应当回答」——假命中或无关内容
        if not key.startswith("relevant"):
            return TopicMatch(
                matched=False, question="", index=-1,
                confidence_score=score, confidence_level=level,
                reason=f"Jev 判定为假命中/无关内容，不回复 (confidence={score:.2f})",
                candidates=names,
            )

        try:
            idx = int(key[len("relevant"):])
        except ValueError:
            idx = -1
        if idx < 0 or idx >= len(usable):
            return TopicMatch(
                matched=False, question="", index=-1,
                confidence_score=score, confidence_level=level,
                reason=f"Jev 返回了越界的候选下标: {key}",
                candidates=names,
            )

        chosen = usable[idx]
        return TopicMatch(
            matched=True,
            question=str(chosen.get("question") or ""),
            index=idx,
            confidence_score=score,
            confidence_level=level,
            reason=f"Jev 判定确实在询问「{chosen.get('question')}」(confidence={score:.2f})",
            candidates=names,
        )
