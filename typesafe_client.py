import asyncio
import logging
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

plugin_dir = str(Path(__file__).parent.resolve())
if plugin_dir not in sys.path:
    sys.path.insert(0, plugin_dir)

try:
    from .utils import SlidingWindowRateLimiter, SimpleTTLCache, normalize_failure_mode, normalize_typesafe_model
except (ImportError, ValueError):
    from utils import SlidingWindowRateLimiter, SimpleTTLCache, normalize_failure_mode, normalize_typesafe_model

logger = logging.getLogger("astrbot")


class TypeSafeClientWrapper:
    """封装与 TypeSafe API 的异步交互、超时控制、限流与异常容错"""

    def __init__(
        self,
        api_key: str = "",
        timeout: int = 10,
        rate_limit_per_minute: int = 60,
        enable_cache: bool = False,
        cache_ttl: int = 60,
        failure_mode: str = "silent",
        model: str = "jev-latest",
    ):
        self.api_key = api_key.strip() if api_key else ""
        self.timeout = float(timeout) if timeout and timeout > 0 else 10.0
        self.failure_mode = normalize_failure_mode(failure_mode)  # silent, rule_based, pass_to_astrbot
        self.model = normalize_typesafe_model(model)
        self.rate_limiter = SlidingWindowRateLimiter(rate_limit_per_minute)
        self.enable_cache = enable_cache
        self.cache = SimpleTTLCache(ttl_seconds=cache_ttl) if enable_cache else None

        self._client: Optional[AsyncTypeSafeClient] = None
        self._init_client()

    def _init_client(self):
        if self.api_key:
            self._client = AsyncTypeSafeClient(
                api_key=self.api_key,
                timeout=self.timeout,
            )
        else:
            self._client = None

    def update_config(
        self,
        api_key: str,
        timeout: int,
        rate_limit_per_minute: int,
        enable_cache: bool,
        cache_ttl: int,
        failure_mode: str,
        model: str = "jev-latest",
    ):
        """配置动态热重载"""
        old_key = self.api_key
        old_timeout = self.timeout
        self.api_key = api_key.strip() if api_key else ""
        self.timeout = float(timeout) if timeout and timeout > 0 else 10.0
        self.failure_mode = normalize_failure_mode(failure_mode)
        self.model = normalize_typesafe_model(model)

        if self.rate_limiter.limit_per_minute != rate_limit_per_minute:
            self.rate_limiter = SlidingWindowRateLimiter(rate_limit_per_minute)

        self.enable_cache = enable_cache
        if enable_cache:
            if not self.cache or self.cache.ttl_seconds != cache_ttl:
                self.cache = SimpleTTLCache(ttl_seconds=cache_ttl)
        else:
            self.cache = None

        if old_key != self.api_key or old_timeout != self.timeout:
            self._init_client()

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
                logger.debug(f"[TypeSafe] 命中了本地缓存结果: {cache_key_text[:30]}...")
                return cached

        # 2. 检查配置
        if not self.is_configured():
            logger.warning("[TypeSafe] API Key 未配置或客户端未初始化")
            return self._handle_failure("api_key_missing")

        # 3. 检查速率限制
        if not self.rate_limiter.allow_request():
            logger.warning("[TypeSafe] 达到每分钟请求限制，跳过本次请求")
            return self._handle_failure("rate_limit_exceeded")

        # 4. 执行异步 API 调用
        try:
            assert self._client is not None
            logger.info(f"[TypeSafe] [2/4 调用TypeSafe] 正在向 API 请求分析 (模型: {self.model})...")
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
            logger.warning(f"[TypeSafe] API 调用超时 (超限 {self.timeout}s)")
            return self._handle_failure("timeout")
        except TypeSafeAPITimeoutError as e:
            logger.warning(f"[TypeSafe] API 超时错误: {e}")
            return self._handle_failure("timeout")
        except TypeSafeRateLimitError as e:
            logger.warning(f"[TypeSafe] 触发服务端 429 速率限制: {e}")
            return self._handle_failure("rate_limit_429")
        except TypeSafeAuthenticationError as e:
            logger.error(f"[TypeSafe] API Key 鉴权失败: {e}")
            return self._handle_failure("auth_failed")
        except TypeSafeError as e:
            logger.error(f"[TypeSafe] TypeSafe 接口返回错误: {e}")
            return self._handle_failure(f"typesafe_error: {e}")
        except Exception as e:
            logger.error(f"[TypeSafe] 未知网络或系统异常: {e}", exc_info=True)
            return self._handle_failure(f"exception: {e}")

    def _handle_failure(self, reason: str) -> Dict[str, Any]:
        """根据 failure_mode 返回降级结果"""
        logger.info(f"[TypeSafe] 按照 failure_mode='{self.failure_mode}' 处理异常 (原因: {reason})")
        return {
            "success": False,
            "error_reason": reason,
            "failure_mode": self.failure_mode,
        }

    async def test_api_connection(self) -> Dict[str, Any]:
        """用于 /typesafe_status 或 /typesafe_test 检测 API 是否通畅"""
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
            return {"ok": True, "message": f"API 连接正常 (模型: {self.model})", "data": resp}
        except Exception as e:
            return {"ok": False, "message": f"连接失败: {str(e)}"}
