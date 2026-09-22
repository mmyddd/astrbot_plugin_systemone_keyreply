# -*- coding: utf-8 -*-
"""测试用依赖桩。

在缺少 astrbot / typesafe_sdk / quart 的环境里，向 sys.modules 注入最小可用替身，
让测试可以直接跑（无需手工准备 PYTHONPATH）：

    python tests/test_plugin.py

若真实依赖已安装（生产 AstrBot 环境），本模块不做任何事，测试跑在真实实现上。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types

# 仓库根目录：让 `import filters` / `import main` 不依赖调用时的 cwd
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

# 桩数据目录：main.py 的 _plugin_data_dir() 会读这个环境变量
_FALLBACK_DATA_DIR = tempfile.mkdtemp(prefix="ts_fallback_")
DATA_DIR = os.environ.setdefault("STUB_DATA", tempfile.mkdtemp(prefix="ts_stub_"))

DEFAULT_BASE_URL = "https://api.typesafe.ai"
SYSTEM_ONE_PATH = "/v1/systemone"


def _available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _new(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__dict__['__stub__'] = True
    sys.modules[name] = mod
    return mod


# ═══════════════════════════════════════════════════════════════
# typesafe_sdk 桩
# ═══════════════════════════════════════════════════════════════


def _install_typesafe_sdk() -> None:
    mod = _new('typesafe_sdk')

    class TypeSafeError(Exception):
        pass

    class TypeSafeAPITimeoutError(TypeSafeError):
        pass

    class TypeSafeRateLimitError(TypeSafeError):
        pass

    class TypeSafeAuthenticationError(TypeSafeError):
        pass

    class TypeSafeAPIError(TypeSafeError):
        pass

    class Choice:
        def __init__(self, instructions, criteria=None):
            self.instructions = instructions
            self.criteria = criteria or {}

    class Noul:
        def __init__(self, instructions):
            self.instructions = instructions

    class Score:
        def __init__(self, instructions, criteria=None):
            self.instructions = instructions
            self.criteria = criteria or []

    class _Response:
        def __init__(self, model=None, nouls=None, choices=None, scores=None):
            self.model = model
            self.nouls = nouls or {}
            self.choices = choices or {}
            self.scores = scores or {}

    class AsyncTypeSafeClient:
        """最小替身：只保留 base_url / 请求地址拼接这一条真实语义。"""
        
        #: 置为 False 可模拟「旧版 SDK 不认识 base_url 关键字」
        SUPPORTS_BASE_URL = True

        def __init__(self, *, api_key=None, model=None, retry=None, timeout=None,
                     headers=None, transport=None, http_client=None, base_url=None,
                     **kwargs):
            if base_url is not None and not type(self).SUPPORTS_BASE_URL:
                raise TypeError(
                    "AsyncTypeSafeClient.__init__() got an unexpected keyword argument 'base_url'"
                )
            self.api_key = api_key or os.environ.get('TYPESAFE_API_KEY')
            self.timeout = timeout
            self.base_url = base_url or os.environ.get('TYPESAFE_BASE_URL') or DEFAULT_BASE_URL
            self.last_request_url = None
            self.call_count = 0

        async def system_one(self, state, questions, *, model=None, retry=None,
                             timeout=None, extra_headers=None, extra_body=None,
                             response_model=None):
            # 复刻真实 SDK：base_url + /v1/systemone
            self.last_request_url = self.base_url.rstrip('/') + SYSTEM_ONE_PATH
            self.call_count += 1
            return _Response(model=model or 'jev-latest')

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    for name, obj in (
        ('TypeSafeError', TypeSafeError),
        ('TypeSafeAPITimeoutError', TypeSafeAPITimeoutError),
        ('TypeSafeRateLimitError', TypeSafeRateLimitError),
        ('TypeSafeAuthenticationError', TypeSafeAuthenticationError),
        ('TypeSafeAPIError', TypeSafeAPIError),
        ('TypeSafeClient', AsyncTypeSafeClient),
        ('AsyncTypeSafeClient', AsyncTypeSafeClient),
        ('Choice', Choice),
        ('Noul', Noul),
        ('Score', Score),
    ):
        setattr(mod, name, obj)


# ═══════════════════════════════════════════════════════════════
# quart 桩
# ═══════════════════════════════════════════════════════════════


def _install_quart() -> None:
    """quart 桩：测试通过 quart._Req._payload 直接指定请求体。

    quart 常常已被 AstrBot 一并装好，此时不再整体替换模块，只补上 _Req 并接管
    request，避免误伤真实环境里的其他导入。
    """
    if _available('quart'):
        import quart as mod  # noqa: PLC0415
    else:
        mod = _new('quart')

    class _Req:
        _payload = {}

        async def get_json(self, silent=True):
            return dict(type(self)._payload)

    mod._Req = _Req
    mod.request = _Req()


# ═══════════════════════════════════════════════════════════════
# astrbot 桩
# ═══════════════════════════════════════════════════════════════


class StubLogger:
    """最小日志替身：记录所有消息，便于测试断言。

    刻意不用标准库的日志设施——插件本体要求日志统一来自 astrbot.api，
    测试也跟着守同一条规矩，仓库里就不会出现自定义 logger 的写法。
    """

    def __init__(self):
        self.records = []
        self.handlers = []

    def _log(self, level, msg, *args):
        text = str(msg)
        if args:
            try:
                text = text % args
            except Exception:
                pass
        self.records.append(text)
        for handler in list(self.handlers):
            emit = getattr(handler, 'emit_record', None)
            if emit is not None:
                emit(level, text)

    def debug(self, msg, *a, **k):
        self._log('DEBUG', msg, *a)

    def info(self, msg, *a, **k):
        self._log('INFO', msg, *a)

    def warning(self, msg, *a, **k):
        self._log('WARNING', msg, *a)

    warn = warning

    def error(self, msg, *a, **k):
        self._log('ERROR', msg, *a)

    def exception(self, msg, *a, **k):
        self._log('ERROR', msg, *a)

    def critical(self, msg, *a, **k):
        self._log('CRITICAL', msg, *a)

    def addHandler(self, handler):
        self.handlers.append(handler)

    def removeHandler(self, handler):
        if handler in self.handlers:
            self.handlers.remove(handler)

    def isEnabledFor(self, level):
        return True


def _install_astrbot() -> None:
    root = _new('astrbot')
    api = _new('astrbot.api')
    api.logger = StubLogger()

    class AstrBotConfig(dict):
        """近似 AstrBotConfig：dict 语义 + save_config()。"""

        def save_config(self):
            return None

    api.AstrBotConfig = AstrBotConfig
    root.api = api

    event = _new('astrbot.api.event')

    class AstrMessageEvent:
        pass

    class MessageEventResult:
        pass

    class _Filter:
        class EventMessageType:
            GROUP_MESSAGE = 'group'
            PRIVATE_MESSAGE = 'private'
            ALL = 'all'

        @staticmethod
        def command(*a, **k):
            def deco(fn):
                return fn
            return deco

        @staticmethod
        def event_message_type(*a, **k):
            def deco(fn):
                return fn
            return deco

    event.filter = _Filter
    event.AstrMessageEvent = AstrMessageEvent
    event.MessageEventResult = MessageEventResult
    event.EventMessageType = _Filter.EventMessageType
    api.event = event

    star = _new('astrbot.api.star')

    class Context:
        def __init__(self, *a, **k):
            pass

    class Star:
        def __init__(self, context=None):
            self.context = context

    def register(*a, **k):
        def deco(cls):
            return cls
        return deco

    star.Context = Context
    star.Star = Star
    star.register = register
    api.star = star

    comps = _new('astrbot.api.message_components')

    class Plain:
        def __init__(self, text=''):
            self.text = text

    class Image:
        def __init__(self, url=''):
            self.url = url

        @classmethod
        def fromURL(cls, url=None, **k):
            return cls(url or '')

    comps.Plain = Plain
    comps.Image = Image
    api.message_components = comps

    core = _new('astrbot.core')
    core_utils = _new('astrbot.core.utils')
    path_mod = _new('astrbot.core.utils.astrbot_path')

    def get_astrbot_data_path():
        return os.environ.get('STUB_DATA') or _FALLBACK_DATA_DIR

    path_mod.get_astrbot_data_path = get_astrbot_data_path
    core_utils.astrbot_path = path_mod
    core.utils = core_utils

    core_star = _new('astrbot.core.star')
    star_mod = _new('astrbot.core.star.star')
    star_mod.star_registry = []
    core_star.star = star_mod
    core.star = core_star
    root.core = core


def install() -> None:
    """安装三个依赖替身，且【一律】接管，不因为真实包存在就让位。

    必须无条件替换的原因：
    1. 真实 astrbot 会按 cwd 解析数据目录（get_astrbot_data_path），
       于是 STUB_DATA 失效、测试写入的问答表会落进仓库的 data/ 里，
       既不隔离，也可能覆盖本地运行数据；
    2. 本插件的测试全部使用手工构造的假事件与假平台，本就不需要真实 AstrBot。
    """
    _install_typesafe_sdk()
    _install_quart()
    _install_astrbot()


install()