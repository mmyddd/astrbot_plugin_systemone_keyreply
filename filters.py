import re
from typing import List, Tuple, Optional, Union, Iterable, Set


class MessageFilter:
    """本地规则过滤器，优先于 TypeSafe AI 执行"""

    COMMAND_PREFIXES: Tuple[str, ...] = ("/", "#", "!", "！", "。", ".", "／")

    # 常见纯媒体/表情/代码消息占位符或标签
    MEDIA_PLACEHOLDER_REGEX = re.compile(
        r"^(\[(图片|动画表情|表情|语音|视频|文件|分享|xml代码|json代码|合并转发)\]|"
        r"\[CQ:(image|record|video|share|forward|xml|json)[^\]]*\]|\s)+$",
        re.IGNORECASE,
    )

    @classmethod
    def is_pure_media_message(cls, text: str) -> bool:
        """判断是否为纯媒体/纯表情/CQ码消息（无实际有效对话文本）"""
        if not text:
            return True
        stripped = text.strip()
        if not stripped:
            return True
        return bool(cls.MEDIA_PLACEHOLDER_REGEX.fullmatch(stripped))

    @staticmethod
    def check_whitelist_blacklist(
        session_id: Union[str, Iterable[str]],
        user_id: str,
        filter_mode: str,
        session_whitelist: Union[List[str], Set[str]],
        session_blacklist: Union[List[str], Set[str]],
        user_whitelist: Union[List[str], Set[str]],
        user_blacklist: Union[List[str], Set[str]],
    ) -> Tuple[bool, Optional[str]]:
        """
        检查黑白名单，优先级：黑名单 > 白名单 > 普通规则
        支持传入单个 session_id 或多个候选会话标识（如 [group_id, session_id, umo]）。
        """
        # 提取会话候选标识集合
        if isinstance(session_id, str):
            session_candidates = {session_id.strip()} if session_id.strip() else set()
        elif isinstance(session_id, set):
            session_candidates = session_id
        else:
            session_candidates = {str(x).strip() for x in session_id if str(x).strip()}

        user_id_str = str(user_id or "").strip()

        # 转换为 set（若调用方已传入 set 则直接复用，避免重复构建）
        session_bl_set = (
            session_blacklist
            if isinstance(session_blacklist, set)
            else {str(x).strip() for x in session_blacklist if str(x).strip()}
        )
        user_bl_set = (
            user_blacklist
            if isinstance(user_blacklist, set)
            else {str(x).strip() for x in user_blacklist if str(x).strip()}
        )
        session_wl_set = (
            session_whitelist
            if isinstance(session_whitelist, set)
            else {str(x).strip() for x in session_whitelist if str(x).strip()}
        )
        user_wl_set = (
            user_whitelist
            if isinstance(user_whitelist, set)
            else {str(x).strip() for x in user_whitelist if str(x).strip()}
        )

        # 1. 黑名单检查（绝对优先）
        matched_session_bl = session_candidates.intersection(session_bl_set)
        if matched_session_bl:
            return False, f"session_in_blacklist({next(iter(matched_session_bl))})"

        if user_id_str and user_id_str in user_bl_set:
            return False, f"user_in_blacklist({user_id_str})"

        # 归一化 filter_mode
        mode = str(filter_mode).strip().lower()
        if "all" in mode or "全" in mode or mode == "blacklist_only" or "仅黑" in mode:
            return True, None

        if "whitelist" in mode or "仅白" in mode or "both" in mode or "并用" in mode:
            # 只要命中会话白名单或用户白名单任意一个即视为在白名单中
            in_session_wl = (
                bool(session_candidates.intersection(session_wl_set))
                if session_wl_set
                else False
            )
            in_user_wl = (
                (user_id_str in user_wl_set)
                if (user_id_str and user_wl_set)
                else False
            )

            if not session_wl_set and not user_wl_set:
                return False, "whitelist_empty_mode_restrictive"

            if in_session_wl or in_user_wl:
                return True, None
            return False, "not_in_whitelist"

        return True, None

    @staticmethod
    def check_length(
        text: str, min_len: int = 2, max_len: int = 2000
    ) -> Tuple[bool, Optional[str]]:
        """检查消息文本长度是否在合理区间"""
        length = len(text.strip())
        if length < min_len:
            return False, f"message_too_short({length}<{min_len})"
        if max_len > 0 and length > max_len:
            return False, f"message_too_long({length}>{max_len})"
        return True, None

    @classmethod
    def is_command_message(cls, text: str) -> bool:
        """判断是否为指令类消息"""
        stripped = text.strip()
        if not stripped:
            return False
        return any(stripped.startswith(p) for p in cls.COMMAND_PREFIXES)

    @staticmethod
    def matches_keywords(text: str, keywords: List[str]) -> bool:
        """检查文本是否包含关键词列表中的任一词"""
        if not text or not keywords:
            return False
        for kw in keywords:
            if kw and kw in text:
                return True
        return False

    @staticmethod
    def matches_regex(text: str, pattern: Union[str, re.Pattern]) -> bool:
        """检查文本是否匹配指定的正则表达式（支持预编译 Pattern 或字符串）"""
        if not text or not pattern:
            return False
        try:
            if isinstance(pattern, re.Pattern):
                return bool(pattern.search(text))
            p_str = str(pattern).strip()
            if not p_str:
                return False
            return bool(re.search(p_str, text, re.IGNORECASE))
        except re.error:
            return False


