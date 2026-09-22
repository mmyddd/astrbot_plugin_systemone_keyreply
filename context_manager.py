import time
from collections import deque
from typing import Dict, List, Optional


class MessageRecord:
    def __init__(
        self,
        sender_id: str,
        sender_name: str,
        content: str,
        is_bot: bool = False,
        is_command: bool = False,
        timestamp: Optional[float] = None,
    ):
        self.sender_id = sender_id
        self.sender_name = sender_name or "群友"
        self.content = content.strip()
        self.is_bot = is_bot
        self.is_command = is_command
        self.timestamp = timestamp or time.time()

    def to_dict(self) -> dict:
        return {
            "sender_name": self.sender_name,
            "sender_id": self.sender_id,
            "content": self.content,
            "is_bot": self.is_bot,
        }


class ContextManager:
    """管理每个会话的近期聊天记录上下文"""

    def __init__(self, max_history_per_session: int = 20):
        self.max_history = max_history_per_session
        self._history: Dict[str, deque[MessageRecord]] = {}
        self._last_access: Dict[str, float] = {}

    def add_message(
        self,
        session_id: str,
        sender_id: str,
        sender_name: str,
        content: str,
        is_bot: bool = False,
        is_command: bool = False,
    ):
        """记录一条消息至会话历史中"""
        if not content:
            return

        if session_id not in self._history:
            # 保护机制：防止活跃会话无限累积造成内存泄露
            if len(self._history) >= 200:
                self.cleanup_inactive_sessions(max_idle_seconds=3600)
                if len(self._history) >= 200:
                    # 仍超出阈值则淘汰访问时间最早的前 20% 会话
                    sorted_sessions = sorted(self._last_access.items(), key=lambda x: x[1])
                    for sid, _ in sorted_sessions[:40]:
                        self.clear_session(sid)

            self._history[session_id] = deque(maxlen=self.max_history)

        record = MessageRecord(
            sender_id=sender_id,
            sender_name=sender_name,
            content=content,
            is_bot=is_bot,
            is_command=is_command,
        )
        self._history[session_id].append(record)
        self._last_access[session_id] = time.time()

    def get_recent_messages(
        self,
        session_id: str,
        count: int = 3,
        ignore_bots: bool = True,
        ignore_commands: bool = True,
    ) -> List[MessageRecord]:
        """获取过滤后的最近上下文消息列表（从旧到新）"""
        if count <= 0 or session_id not in self._history:
            return []

        records = list(self._history[session_id])
        filtered: List[MessageRecord] = []

        # 从最近的向前遍历
        for rec in reversed(records):
            if ignore_bots and rec.is_bot:
                continue
            if ignore_commands and rec.is_command:
                continue
            filtered.append(rec)
            if len(filtered) >= count:
                break

        # 逆序恢复为时间升序（从旧到新）
        return list(reversed(filtered))

    def format_context_string(self, records: List[MessageRecord]) -> str:
        """格式化为易读的群聊文本对话流"""
        if not records:
            return "无最近聊天记录"

        lines = []
        for r in records:
            lines.append(f"{r.sender_name}: {r.content}")
        return "\n".join(lines)

    def build_systemone_state(
        self,
        records: List[MessageRecord],
        current_message: str,
        current_sender_name: str,
    ) -> dict:
        """为 TypeSafe System One 构建结构化上下文 State"""
        recent_chat = []
        for r in records:
            recent_chat.append(
                {
                    "sender": r.sender_name,
                    "text": r.content,
                }
            )

        state = {
            "recent_chat": recent_chat,
            "current_message": {
                "sender": current_sender_name or "用户",
                "text": current_message,
            },
        }
        return state

    def clear_session(self, session_id: str):
        self._history.pop(session_id, None)
        self._last_access.pop(session_id, None)

    def cleanup_inactive_sessions(self, max_idle_seconds: int = 3600):
        """清理长时间不活跃的会话数据"""
        now = time.time()
        inactive = [
            sid
            for sid, last_t in self._last_access.items()
            if now - last_t > max_idle_seconds
        ]
        for sid in inactive:
            self.clear_session(sid)
