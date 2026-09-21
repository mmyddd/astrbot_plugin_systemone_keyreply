import time
import random
import hashlib
from typing import Dict, Tuple, Optional, Any


class CooldownTracker:
    """管理会话和用户的冷却时间与连续回复限制"""

    def __init__(self):
        self._session_last_reply: Dict[str, float] = {}
        self._user_last_reply: Dict[str, float] = {}
        self._continuous_reply_count: Dict[str, int] = {}
        self._intervening_messages: Dict[str, int] = {}

    def is_cooling_down(
        self,
        session_id: str,
        user_id: str,
        session_cooldown: int,
        user_cooldown: int,
        bypass: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """
        检查是否在冷却时间内。
        :param bypass: 是否绕过冷却（例如显式 @ 机器人）
        :return: (is_cooling, reason)
        """
        if bypass:
            return False, None

        now = time.time()

        if session_id and session_cooldown > 0 and session_id in self._session_last_reply:
            elapsed = now - self._session_last_reply[session_id]
            if elapsed < session_cooldown:
                remaining = int(session_cooldown - elapsed)
                return True, f"session_cooldown({remaining}s remaining)"

        if user_id and user_cooldown > 0 and user_id in self._user_last_reply:
            elapsed = now - self._user_last_reply[user_id]
            if elapsed < user_cooldown:
                remaining = int(user_cooldown - elapsed)
                return True, f"user_cooldown({remaining}s remaining)"

        return False, None

    def is_continuous_limit_reached(
        self, session_id: str, max_continuous: int, bypass: bool = False
    ) -> bool:
        """检查会话中机器人的连续主动回复次数是否达到上限"""
        if bypass or max_continuous <= 0 or not session_id:
            return False
        return self._continuous_reply_count.get(session_id, 0) >= max_continuous

    def record_reply_sent(self, session_id: str, user_id: str):
        """记录一次机器人回复发送，更新时间戳并增加连续回复计数"""
        now = time.time()
        if session_id:
            self._session_last_reply[session_id] = now
            self._continuous_reply_count[session_id] = (
                self._continuous_reply_count.get(session_id, 0) + 1
            )
            self._intervening_messages[session_id] = 0

        if user_id:
            self._user_last_reply[user_id] = now

        # 防止长时间运行导致的会话与用户冷却缓存膨胀
        if len(self._session_last_reply) >= 500 or len(self._user_last_reply) >= 500:
            self.cleanup_old_records()

    def record_user_message(
        self, session_id: str, is_bot: bool = False, session_cooldown: int = 30
    ):
        """
        记录群内用户的正常消息。
        只有当群内自然产生足够多（>=3条）普通消息，或者距上次机器人回复已超出会话冷却时，
        才重置连续回复防刷计数。避免每次用户说话直接归零导致上限防护失效。
        """
        if is_bot or not session_id:
            return

        cnt = self._intervening_messages.get(session_id, 0) + 1
        self._intervening_messages[session_id] = cnt

        now = time.time()
        last_reply = self._session_last_reply.get(session_id, 0)
        cooldown_passed = (last_reply > 0) and (now - last_reply >= session_cooldown)

        if cnt >= 3 or cooldown_passed:
            self._continuous_reply_count[session_id] = 0

    def reset_continuous(self, session_id: str):
        """显式重置某个会话的连续回复计数"""
        if session_id:
            self._continuous_reply_count[session_id] = 0
            self._intervening_messages[session_id] = 0

    def reset_session(self, session_id: str):
        """重置某个会话的全部冷却与计数"""
        self._session_last_reply.pop(session_id, None)
        self._continuous_reply_count.pop(session_id, None)
        self._intervening_messages.pop(session_id, None)

    def cleanup_old_records(self, max_idle_seconds: int = 3600):
        """清理长时间不活跃的冷却记录，防止内存无限累积"""
        now = time.time()
        expired_sessions = [
            sid
            for sid, ts in self._session_last_reply.items()
            if now - ts > max_idle_seconds
        ]
        for sid in expired_sessions:
            self._session_last_reply.pop(sid, None)
            self._continuous_reply_count.pop(sid, None)
            self._intervening_messages.pop(sid, None)

        expired_users = [
            uid
            for uid, ts in self._user_last_reply.items()
            if now - ts > max_idle_seconds
        ]
        for uid in expired_users:
            self._user_last_reply.pop(uid, None)

        # 如果清理过期后仍然超过容量上限（例如 500），强制淘汰时间最早的 20%
        if len(self._session_last_reply) > 500:
            sorted_sids = sorted(self._session_last_reply.items(), key=lambda x: x[1])
            for sid, _ in sorted_sids[: len(sorted_sids) // 5]:
                self._session_last_reply.pop(sid, None)
                self._continuous_reply_count.pop(sid, None)
                self._intervening_messages.pop(sid, None)

        if len(self._user_last_reply) > 500:
            sorted_uids = sorted(self._user_last_reply.items(), key=lambda x: x[1])
            for uid, _ in sorted_uids[: len(sorted_uids) // 5]:
                self._user_last_reply.pop(uid, None)


class SlidingWindowRateLimiter:
    """滑动窗口限流器，用于控制 TypeSafe 每分钟 API 请求数"""

    def __init__(self, limit_per_minute: int = 60):
        self.limit_per_minute = limit_per_minute
        self.timestamps: list[float] = []

    def allow_request(self) -> bool:
        if self.limit_per_minute <= 0:
            return True

        now = time.time()
        window_start = now - 60.0

        # 清除超过 60 秒的历史记录
        self.timestamps = [t for t in self.timestamps if t > window_start]

        if len(self.timestamps) < self.limit_per_minute:
            self.timestamps.append(now)
            return True
        return False

    def current_load(self) -> int:
        now = time.time()
        window_start = now - 60.0
        self.timestamps = [t for t in self.timestamps if t > window_start]
        return len(self.timestamps)


class SimpleTTLCache:
    """轻量级内存 TTL 缓存，用于短时间内相同消息的判断结果复用"""

    def __init__(self, ttl_seconds: int = 60, max_size: int = 1000):
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._cache: Dict[str, Tuple[float, dict]] = {}

    def _hash_key(self, text: str) -> str:
        return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()

    def get(self, text: str) -> Optional[dict]:
        key = self._hash_key(text)
        if key in self._cache:
            ts, val = self._cache[key]
            if time.time() - ts < self.ttl_seconds:
                return val
            else:
                del self._cache[key]
        return None

    def set(self, text: str, value: dict):
        if len(self._cache) >= self.max_size:
            now = time.time()
            expired_keys = [
                k for k, (ts, _) in self._cache.items() if now - ts >= self.ttl_seconds
            ]
            for k in expired_keys:
                del self._cache[k]
            if len(self._cache) >= self.max_size:
                for k in list(self._cache.keys())[: self.max_size // 5]:
                    del self._cache[k]

        key = self._hash_key(text)
        self._cache[key] = (time.time(), value)

    def clear(self):
        self._cache.clear()


def check_probability(probability: int) -> bool:
    """根据概率判定是否回复 (0-100)"""
    if probability >= 100:
        return True
    if probability <= 0:
        return False
    return (random.random() * 100.0) < float(probability)


def calculate_reply_delay(
    enabled: bool,
    mode: str,
    min_delay: float = 1.0,
    max_delay: float = 3.0,
    fixed_delay: float = 2.0,
) -> float:
    """
    计算回复需要延迟的秒数（模拟真人打字思考延时）
    :param enabled: 是否启用延时
    :param mode: 延时模式（随机延时 / 固定延时）
    :param min_delay: 最小随机秒数
    :param max_delay: 最大随机秒数
    :param fixed_delay: 固定延时秒数
    :return: 延时秒数 (float)
    """
    if not enabled:
        return 0.0

    normalized_mode = normalize_reply_delay_mode(mode)
    if normalized_mode == "fixed":
        return max(0.0, float(fixed_delay))
    else:
        # 随机延时
        min_d = max(0.0, float(min_delay))
        max_d = max(min_d, float(max_delay))
        if min_d == max_d:
            return min_d
        return round(random.uniform(min_d, max_d), 2)


def truncate_reply(text: str, max_chars: int) -> str:
    """
    对 LLM 回复做长度保护截断，尽量在标点符号处平滑截断
    """
    if not text or max_chars <= 0:
        return text

    trimmed = text.strip()
    if len(trimmed) <= max_chars:
        return trimmed

    # 截取 max_chars 前半部分，寻找最后一个句子结束标点
    truncated = trimmed[:max_chars]
    punctuation_marks = ["。", "！", "？", "!", "?", "；", ";", "\n"]
    last_punct_idx = -1
    for p in punctuation_marks:
        idx = truncated.rfind(p)
        if idx > last_punct_idx:
            last_punct_idx = idx

    # 如果在最后 25% 范围内找到了标点，则截取至标点处
    if last_punct_idx > int(max_chars * 0.75):
        return truncated[: last_punct_idx + 1]

    # 否则直接截断加省略号
    return truncated + "..."


# ==========================================
# 中英文配置项双向归一化映射工具函数
# ==========================================

def normalize_failure_mode(val: Any) -> str:
    """归一化故障降级模式"""
    s = str(val or "").strip().lower()
    if "rule" in s or "规则" in s or "问号" in s:
        return "rule_based"
    if "pass" in s or "直通" in s or "astrbot" in s:
        return "pass_to_astrbot"
    return "silent"


def normalize_confidence_level(val: Any) -> str:
    """归一化置信度等级"""
    s = str(val or "").strip().lower()
    if "高" in s or "high" in s:
        return "high"
    if "低" in s or "low" in s:
        return "low"
    return "medium"


def normalize_model_mode(val: Any) -> str:
    """归一化模型选择模式"""
    s = str(val or "").strip().lower()
    if "指定" in s or "custom" in s or "provider" in s:
        return "custom"
    return "follow_session"


def normalize_reply_length_mode(val: Any) -> str:
    """归一化回复篇幅模式"""
    s = str(val or "").strip().lower()
    if "极短" in s or "very_short" in s:
        return "very_short"
    if "简短" in s or "short" in s:
        return "short"
    if "正常" in s or "normal" in s:
        return "normal"
    if "详细" in s or "detailed" in s:
        return "detailed"
    if "自定" in s or "自然" in s or "精炼" in s or "custom" in s:
        return "custom"
    return "short"


def normalize_reply_style(val: Any) -> str:
    """归一化回复风格"""
    s = str(val or "").strip().lower()
    mapping = {
        "自然": "natural",
        "natural": "natural",
        "简洁": "concise",
        "concise": "concise",
        "专业": "professional",
        "professional": "professional",
        "友好": "friendly",
        "friendly": "friendly",
        "幽默": "humorous",
        "humorous": "humorous",
        "活泼": "lively",
        "lively": "lively",
        "严谨": "rigorous",
        "rigorous": "rigorous",
        "群友": "group_peer",
        "group_peer": "group_peer",
        "自定义": "custom",
        "custom": "custom",
    }
    for k, v in mapping.items():
        if k in s:
            return v
    return "natural"


def normalize_filter_mode(val: Any) -> str:
    """归一化黑白名单模式"""
    s = str(val or "").strip().lower()
    if "全" in s or "all_allowed" in s:
        return "all_allowed"
    if "仅白" in s or "whitelist_only" in s:
        return "whitelist_only"
    if "并用" in s or "both" in s:
        return "both"
    return "blacklist_only"



def normalize_reply_delay_mode(val: Any) -> str:
    """归一化回复延时模式"""
    s = str(val or "").strip().lower()
    if "固定" in s or "fixed" in s:
        return "fixed"
    return "random"


def normalize_typesafe_model(selected: Any, custom: str = "") -> str:
    """归一化 TypeSafe 模型名称"""
    c = str(custom or "").strip()
    if c:
        return c
    s = str(selected or "").strip().lower()
    if "jev-latest" in s:
        return "jev-latest"
    if "jev" in s:
        return "jev"
    if "自定" in s:
        return c if c else "jev-latest"
    return str(selected).strip() if str(selected).strip() else "jev-latest"
