import asyncio
from typing import Optional, Dict, Any

from typesafe_sdk import (
    AsyncTypeSafeClient,
    TypeSafeAPITimeoutError,
    TypeSafeRateLimitError,
    TypeSafeAuthenticationError,
    TypeSafeError,
)

import sys
from pathlib import Path
from astrbot.api import logger

plugin_dir = str(Path(__file__).parent.resolve())
if plugin_dir not in sys.path:
    sys.path.insert(0, plugin_dir)

try:
    from .utils import (
        DEFAULT_API_BASE_URL,
        SlidingWindowRateLimiter,
        SimpleTTLCache,
        normalize_failure_mode,
        normalize_systemone_base_url,
        normalize_systemone_model,
    )
except (ImportError, ValueError):
    from utils import (
        DEFAULT_API_BASE_URL,
        SlidingWindowRateLimiter,
        SimpleTTLCache,
        normalize_failure_mode,
        normalize_systemone_base_url,
        normalize_systemone_model,
    )



class SystemOneClientWrapper:
    """封装与 SystemOne API 的异步交互、超时控制、限流与异常容错"""

    def __init__(
        self,
        api_key: str = "",
        timeout: int = 10,
        rate_limit_per_minute: int = 60,
        enable_cache: bool = False,
        cache_ttl: int = 60,
        failure_mode: str = "silent",
        model: str = "jev-latest",
        base_url: str = "",
    ):
        self.api_key = api_key.strip() if api_key else ""
        self.timeout = float(timeout) if timeout and timeout > 0 else 10.0
        self.failure_mode = normalize_failure_mode(failure_mode)  # silent, rule_based, pass_to_astrbot
        self.model = normalize_systemone_model(model)
        # 只保存根地址：SDK 会在其后自动拼接 /v1/systemone
        self.base_url = normalize_systemone_base_url(base_url)
        self.base_url_supported = True
        self.rate_limiter = SlidingWindowRateLimiter(rate_limit_per_minute)
        self.enable_cache = enable_cache
        self.cache = SimpleTTLCache(ttl_seconds=cache_ttl) if enable_cache else None

        self._client: Optional[AsyncTypeSafeClient] = None
        self._init_client()

    def _init_client(self):
        """按当前配置创建 SDK 客户端。

        base_url 留空时不传该参数，完全沿用 SDK 默认地址（含 TYPESAFE_BASE_URL
        环境变量）；传值时只给根地址，SDK 会在其后自动拼接 /v1/systemone。
        """
        if not self.api_key:
            self._client = None
            return

        kwargs: Dict[str, Any] = {"api_key": self.api_key, "timeout": self.timeout}
        if self.base_url:
            kwargs["base_url"] = self.base_url

        try:
            self._client = AsyncTypeSafeClient(**kwargs)
            self.base_url_supported = True
        except TypeError as e:
            # 旧版 typesafe-sdk 不认识 base_url 关键字参数
            if "base_url" not in kwargs:
                raise
            self.base_url_supported = False
            logger.warning(
                f"[SystemOne] 当前 typesafe-sdk 不支持自定义 Base URL，已回退官方地址: {e}"
            )
            self._client = AsyncTypeSafeClient(
                api_key=self.api_key,
                timeout=self.timeout,
            )

    def update_config(
        self,
        api_key: str,
        timeout: int,
        rate_limit_per_minute: int,
        enable_cache: bool,
        cache_ttl: int,
        failure_mode: str,
        model: str = "jev-latest",
        base_url: str = "",
    ):
        """配置动态热重载"""
        old_key = self.api_key
        old_timeout = self.timeout
        old_base_url = self.base_url
        self.api_key = api_key.strip() if api_key else ""
        self.timeout = float(timeout) if timeout and timeout > 0 else 10.0
        self.failure_mode = normalize_failure_mode(failure_mode)
        self.model = normalize_systemone_model(model)
        self.base_url = normalize_systemone_base_url(base_url)

        if self.rate_limiter.limit_per_minute != rate_limit_per_minute:
            self.rate_limiter = SlidingWindowRateLimiter(rate_limit_per_minute)

        self.enable_cache = enable_cache
        if enable_cache:
            if not self.cache or self.cache.ttl_seconds != cache_ttl:
                self.cache = SimpleTTLCache(ttl_seconds=cache_ttl)
        else:
            self.cache = None

        if (
            old_key != self.api_key
            or old_timeout != self.timeout
            or old_base_url != self.base_url
        ):
            self._init_client()

    @property
    def effective_base_url(self) -> str:
        """实际生效的 API 根地址（自定义未被 SDK 支持时回退官方默认值）。"""
        if self.base_url and self.base_url_supported:
            return self.base_url
        return DEFAULT_API_BASE_URL

    def is_configured(self) -> bool:
        return bool(self.api_key and self._client)

    async def call_system_one(
        self,
        state: Dict[str, Any],
        questions: Dict[str, Any],
        cache_key_text: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        调用 TypeSafe System One 模型进行结构化判断。
        包含缓存检查、速率限制检查与全面的故障隔离保护。
        """
        # 1. 检查缓存
        if self.enable_cache and self.cache and cache_key_text:
            cached = self.cache.get(cache_key_text)
            if cached is not None:
                logger.debug(f"[SystemOne] 命中了本地缓存结果: {cache_key_text[:30]}...")
                return cached

        # 2. 检查配置
        if not self.is_configured():
            logger.warning("[SystemOne] API Key 未配置或客户端未初始化")
            return self._handle_failure("api_key_missing")

        # 3. 检查速率限制
        if not self.rate_limiter.allow_request():
            logger.warning("[SystemOne] 达到每分钟请求限制，跳过本次请求")
            return self._handle_failure("rate_limit_exceeded")

        # 4. 执行异步 API 调用
        try:
            assert self._client is not None
            logger.info(f"[SystemOne] [2/4 调用 SystemOne] 正在向 API 请求分析 (模型: {self.model})...")
            response = await asyncio.wait_for(
                self._client.system_one(
                    state=state,
                    questions=questions,
                    model=self.model if self.model else None,
                    timeout=self.timeout,
                ),
                timeout=self.timeout + 2.0,  # 稍微宽限防止底层挂起
            )

            result = {
                "success": True,
                "response": response,
                "model": getattr(response, "model", self.model),
                "raw_nouls": response.nouls if hasattr(response, "nouls") else {},
                "raw_choices": response.choices if hasattr(response, "choices") else {},
                "raw_scores": response.scores if hasattr(response, "scores") else {},
            }

            # 写入缓存
            if self.enable_cache and self.cache and cache_key_text:
                self.cache.set(cache_key_text, result)

            return result

        except asyncio.TimeoutError:
            logger.warning(f"[SystemOne] API 调用超时 (超限 {self.timeout}s)")
            return self._handle_failure("timeout")
        except TypeSafeAPITimeoutError as e:
            logger.warning(f"[SystemOne] API 超时错误: {e}")
            return self._handle_failure("timeout")
        except TypeSafeRateLimitError as e:
            logger.warning(f"[SystemOne] 触发服务端 429 速率限制: {e}")
            return self._handle_failure("rate_limit_429")
        except TypeSafeAuthenticationError as e:
            logger.error(f"[SystemOne] API Key 鉴权失败: {e}")
            return self._handle_failure("auth_failed")
        except TypeSafeError as e:
            logger.error(f"[SystemOne] SystemOne 接口返回错误: {e}")
            return self._handle_failure(f"systemone_error: {e}")
        except Exception as e:
            logger.error(f"[SystemOne] 未知网络或系统异常: {e}", exc_info=True)
            return self._handle_failure(f"exception: {e}")

    def _handle_failure(self, reason: str) -> Dict[str, Any]:
        """根据 failure_mode 返回降级结果"""
        logger.info(f"[SystemOne] 按照 failure_mode='{self.failure_mode}' 处理异常 (原因: {reason})")
        return {
            "success": False,
            "error_reason": reason,
            "failure_mode": self.failure_mode,
        }

    async def test_api_connection(self) -> Dict[str, Any]:
        """用于 /systemone_status 或 /systemone_test 检测 API 是否通畅"""
        if not self.is_configured():
            return {"ok": False, "message": "API Key 未填写"}

        from typesafe_sdk import Choice

        state = {"test_message": "Hello"}
        questions = {
            "ping": Choice(
                instructions="Is this a test message?",
                criteria={"yes": "A test greeting", "no": "Not a test"},
            )
        }

        try:
            assert self._client is not None
            resp = await asyncio.wait_for(
                self._client.system_one(
                    state=state,
                    questions=questions,
                    model=self.model if self.model else None,
                    timeout=5.0,
                ),
                timeout=7.0,
            )
            return {
                "ok": True,
                "message": (
                    f"API 连接正常 (模型: {self.model}, 地址: {self.effective_base_url})"
                ),
                "base_url": self.effective_base_url,
                "data": resp,
            }
        except Exception as e:
            return {
                "ok": False,
                "message": f"连接失败 ({self.effective_base_url}): {str(e)}",
                "base_url": self.effective_base_url,
            }
