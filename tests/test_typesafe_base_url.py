# -*- coding: utf-8 -*-
"""TypeSafe API Base URL 配置测试。

覆盖：
- normalize_typesafe_base_url 的归一化规则（补协议头 / 去尾斜杠 / 剥离误填的接口路径）
- 包装器只把【根地址】交给 SDK，/v1/systemone 由 SDK 自行拼接
- 旧版 typesafe-sdk 不认识 base_url 时回退官方地址并告警
- update_config 切换地址会重建客户端
- main.py 接线：配置 → 插件实例 → 客户端 → 状态 API
- schema / 页面 / 可编辑白名单三处同步，且提示文案写明了自动追加后缀

运行： python tests/test_typesafe_base_url.py
"""
import _stubs  # noqa: F401  注入 astrbot / typesafe_sdk / quart 桩

import asyncio
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import typesafe_client as TC
from utils import DEFAULT_TYPESAFE_BASE_URL, normalize_typesafe_base_url

REPO = Path(__file__).resolve().parent.parent
SYSTEM_ONE_PATH = "/v1/systemone"

results = []


def check(name, ok, extra=""):
    results.append((name, bool(ok), str(extra)[:200]))


# ═══════════════════════════════════════════════════════════════
# SDK 替身：记录构造参数与最终请求地址
# ═══════════════════════════════════════════════════════════════


class _Resp:
    model = "jev-latest"
    nouls = {}
    choices = {}
    scores = {}


class FakeSDK:
    def __init__(self, *, api_key=None, timeout=None, base_url=None, **kwargs):
        self.api_key = api_key
        self.timeout = timeout
        self.passed_base_url = base_url
        self.base_url = base_url or DEFAULT_TYPESAFE_BASE_URL
        self.last_request_url = None
        self.calls = 0

    async def system_one(self, state, questions, *, model=None, **kwargs):
        # 复刻真实 SDK：url = base_url + /v1/systemone
        self.last_request_url = self.base_url.rstrip("/") + SYSTEM_ONE_PATH
        self.calls += 1
        return _Resp()


class OldSDK:
    """模拟旧版 SDK：构造函数不认识 base_url 关键字。"""

    def __init__(self, *, api_key=None, timeout=None, **kwargs):
        if kwargs:
            raise TypeError(
                "__init__() got an unexpected keyword argument 'base_url'"
            )
        self.base_url = DEFAULT_TYPESAFE_BASE_URL
        self.api_key = api_key
        self.timeout = timeout

    async def system_one(self, state, questions, *, model=None, **kwargs):
        return _Resp()


_ORIGINAL_SDK = TC.AsyncTypeSafeClient


def make_wrapper(**overrides):
    kwargs = {"api_key": "ts_key_1234567890", "timeout": 5}
    kwargs.update(overrides)
    return TC.TypeSafeClientWrapper(**kwargs)


async def run():
    # ── 1. 归一化规则 ────────────────────────────────────────────
    check("空值表示不覆盖", normalize_typesafe_base_url("") == "")
    check("纯空白视为空", normalize_typesafe_base_url("   ") == "")
    check("None 视为空", normalize_typesafe_base_url(None) == "")
    official = "https://api.typesafe.ai"
    check("官方地址原样保留", normalize_typesafe_base_url(official) == official)
    check("去掉结尾斜杠", normalize_typesafe_base_url(official + "/") == official)
    check("剥离 /v1/systemone",
          normalize_typesafe_base_url(official + "/v1/systemone") == official)
    check("剥离带斜杠的 /v1/systemone",
          normalize_typesafe_base_url(official + "/v1/systemone/") == official)
    check("剥离 /v1/models", normalize_typesafe_base_url(official + "/v1/models") == official)
    check("剥离裸 /v1", normalize_typesafe_base_url(official + "/v1") == official)
    check("缺少协议头自动补 https",
          normalize_typesafe_base_url("api.typesafe.ai") == official)
    check("保留 http 协议",
          normalize_typesafe_base_url("http://localhost:8080") == "http://localhost:8080")
    check("保留反代前缀",
          normalize_typesafe_base_url("https://gw.example.com/ts/")
          == "https://gw.example.com/ts")
    check("粘贴时带了说明文字只取第一段",
          normalize_typesafe_base_url("https://gw.example.com/ts  # 备用")
          == "https://gw.example.com/ts")
    check("反代路径 + 误填后缀一并纠正",
          normalize_typesafe_base_url("https://gw.example.com/ts/v1/systemone")
          == "https://gw.example.com/ts")

    # ── 2. 只把根地址交给 SDK，路径由 SDK 拼接 ──────────────────
    TC.AsyncTypeSafeClient = FakeSDK
    wrapper = make_wrapper()
    await wrapper.call_system_one(state={}, questions={})
    check("留空时不覆盖 SDK 默认地址", wrapper.base_url == "")
    check("留空时不传 base_url 给 SDK", wrapper._client.passed_base_url is None)
    check("留空时生效地址为官方默认", wrapper.effective_base_url == official)
    check("留空时请求官方 /v1/systemone",
          wrapper._client.last_request_url == official + SYSTEM_ONE_PATH,
          wrapper._client.last_request_url)

    gateway = "https://gw.example.com/ts"
    wrapper2 = make_wrapper(base_url=gateway + "/v1/systemone")
    await wrapper2.call_system_one(state={}, questions={})
    check("误填的完整接口地址被收敛为根地址", wrapper2.base_url == gateway, wrapper2.base_url)
    check("交给 SDK 的是根地址", wrapper2._client.passed_base_url == gateway,
          wrapper2._client.passed_base_url)
    check("最终请求地址由 SDK 自动补 /v1/systemone",
          wrapper2._client.last_request_url == gateway + SYSTEM_ONE_PATH,
          wrapper2._client.last_request_url)
    check("生效地址即配置的根地址", wrapper2.effective_base_url == gateway)

    # ── 3. 旧版 SDK 回退 ────────────────────────────────────────
    TC.AsyncTypeSafeClient = OldSDK
    probe = _stubs.StubLogger()
    saved_logger = TC.logger
    TC.logger = probe
    try:
        old = make_wrapper(base_url=gateway)
    finally:
        TC.logger = saved_logger
    check("旧版 SDK 下客户端仍可用", old.is_configured())
    check("旧版 SDK 下标记不支持自定义地址", old.base_url_supported is False)
    check("旧版 SDK 下生效地址回退官方", old.effective_base_url == official,
          old.effective_base_url)
    check("旧版 SDK 回退时给出告警",
          any("Base URL" in m for m in probe.records), probe.records[-3:])

    # ── 4. update_config 切换地址 ───────────────────────────────
    TC.AsyncTypeSafeClient = FakeSDK
    hot = make_wrapper(base_url=gateway)
    first_client = hot._client
    hot.update_config(
        api_key="ts_key_1234567890", timeout=5, rate_limit_per_minute=60,
        enable_cache=False, cache_ttl=60, failure_mode="silent",
        model="jev-latest", base_url="https://other.example.com",
    )
    check("热重载后 base_url 已更新", hot.base_url == "https://other.example.com", hot.base_url)
    check("热重载后重建了客户端", hot._client is not first_client)
    check("热重载后生效地址随之更新",
          hot.effective_base_url == "https://other.example.com")

    # ── 5. main.py 接线 ─────────────────────────────────────────
    root = Path(tempfile.gettempdir()) / "ts_baseurl"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    os.environ["STUB_DATA"] = str(root)
    import main as M

    ctx = type("C", (), {"register_web_api": lambda *a, **k: None})()
    cfg = {"typesafe_api_key": "ts_key_1234567890",
           "typesafe_base_url": "https://gw.example.com/ts/v1/systemone/"}
    plugin = M.TypeSafeAutoReplyPlugin(ctx, cfg)
    check("插件实例归一化了 base_url", plugin.typesafe_base_url == gateway,
          plugin.typesafe_base_url)
    check("插件把 base_url 透传给了客户端",
          plugin.typesafe_client._client.passed_base_url == gateway,
          plugin.typesafe_client._client.passed_base_url)

    status = await plugin._api_status()
    check("状态接口暴露 base_url", status.get("base_url") == gateway, status.get("base_url"))
    check("状态接口暴露生效地址", status.get("base_url_effective") == gateway)
    check("状态接口暴露支持标记", status.get("base_url_supported") is True)
    check("状态接口版本与 metadata 一致", status.get("version") == "1.0.2",
          status.get("version"))

    # 留空时状态接口应显示官方默认地址
    plugin2 = M.TypeSafeAutoReplyPlugin(ctx, {"typesafe_api_key": "ts_key_1234567890"})
    status2 = await plugin2._api_status()
    check("未配置时 base_url 为空串", status2.get("base_url") == "", status2.get("base_url"))
    check("未配置时生效地址为官方默认", status2.get("base_url_effective") == official,
          status2.get("base_url_effective"))

    # ── 6. 配置三处同步 + 文案注明自动追加后缀 ──────────────────
    schema = json.loads((REPO / "_conf_schema.json").read_text(encoding="utf-8"))
    cfg_js = (REPO / "pages" / "typesafe-console" / "js" / "config.js").read_text(encoding="utf-8")
    main_py = (REPO / "main.py").read_text(encoding="utf-8")
    meta = (REPO / "metadata.yaml").read_text(encoding="utf-8")

    item = schema.get("typesafe_base_url") or {}
    check("schema 存在 typesafe_base_url", bool(item))
    check("schema 默认值为空串", item.get("default") == "")
    check("schema 提示写明自动追加 /v1/systemone",
          "/v1/systemone" in str(item.get("hint", "")), item.get("hint"))
    check("页面有对应控件", "key: 'typesafe_base_url'" in cfg_js)
    page_field = re.search(r"key: 'typesafe_base_url'.*?\}", cfg_js)
    check("页面描述写明自动追加 /v1/systemone",
          bool(page_field) and "/v1/systemone" in page_field.group(0))
    check("字段在可编辑白名单内", '"typesafe_base_url"' in main_py)


TC.AsyncTypeSafeClient = _ORIGINAL_SDK
asyncio.run(run())

print("")
for name, ok, extra in results:
    if not ok:
        print("  FAIL:", name, "->", extra)
passed = sum(1 for _, ok, _ in results if ok)
print(f"PASSED {passed}/{len(results)}")
raise SystemExit(0 if passed == len(results) else 1)
