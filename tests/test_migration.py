# -*- coding: utf-8 -*-
"""改名迁移测试：v1.0.3 起插件与配置键由 typesafe_* 统一为 systemone_*。

覆盖：
- 只有旧配置键（typesafe_*）的老配置：升级后能读到新键（systemone_*）；
- 新旧键并存：以新键为准，不被旧值覆盖；
- 只有旧数据目录（plugin_data/astrbot_plugin_typesafe_keyreply/）：问答表自动
  复制到新目录并被 QAStore 装载，旧文件原样保留；
- 新目录已有问答表时不覆盖；
- 全新安装（既无旧键也无旧目录）不受影响。

运行：python tests/test_migration.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(tempfile.gettempdir()) / "ts_migrate"
shutil.rmtree(ROOT, ignore_errors=True)
ROOT.mkdir(parents=True)
os.environ["STUB_DATA"] = str(ROOT)

import _stubs  # noqa: F401  注入 astrbot / typesafe_sdk / quart 桩
import main as M

TESTS_DIR = Path(__file__).resolve().parent
# 这份夹具文件夹故意保留【旧插件名】：它就是升级前的数据目录
OLD_PLUGIN_NAME = "astrbot_plugin_typesafe_keyreply"
FIXTURE = TESTS_DIR / "data" / "plugin_data" / OLD_PLUGIN_NAME / "qa_tables.json"

results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond), str(extra)[:160]))


ctx = type("C", (), {"register_web_api": lambda *a, **k: None})()

# ── 1. 还原「升级前」现场：只有旧数据目录 + 只有旧配置键 ──────────────────
legacy_dir = ROOT / "plugin_data" / OLD_PLUGIN_NAME
legacy_dir.mkdir(parents=True)
legacy_file = legacy_dir / "qa_tables.json"
shutil.copy(FIXTURE, legacy_file)
fixture_tables = json.loads(legacy_file.read_text(encoding="utf-8"))["tables"]

new_dir = ROOT / "plugin_data" / M.PLUGIN_NAME
new_file = new_dir / "qa_tables.json"

LEGACY_CFG = {
    "typesafe_api_key": "sk_legacy_1234567890",
    "typesafe_base_url": "https://gw.example.com/ts/v1/systemone/",
    "typesafe_timeout": 17,
    "typesafe_model": "自定义模型",
    "typesafe_custom_model": "custom-jev-v2",
    "enable_reply_delay": False,
}

check("插件名已改为 systemone", M.PLUGIN_NAME == "astrbot_plugin_systemone_keyreply", M.PLUGIN_NAME)
check("夹具确实是旧插件名目录", FIXTURE.exists() and FIXTURE.parent.name != M.PLUGIN_NAME, str(FIXTURE))
check("升级前只有旧数据目录", not new_dir.exists(), str(new_dir))

p = M.SystemOneKeyReplyPlugin(ctx, dict(LEGACY_CFG))

# ── 2. 配置键迁移 ───────────────────────────────────────────────────
check("旧 api_key 已迁移", p.systemone_api_key == "sk_legacy_1234567890", p.systemone_api_key)
check("旧 base_url 已迁移并归一化",
      p.systemone_base_url == "https://gw.example.com/ts", p.systemone_base_url)
check("旧 timeout 已迁移", p.systemone_timeout == 17, p.systemone_timeout)
check("旧 custom_model 已迁移",
      p.systemone_custom_model == "custom-jev-v2", p.systemone_custom_model)
check("旧 model 选择已迁移", p.systemone_model_raw == "自定义模型", p.systemone_model_raw)
check("模型按自定义值归一化", p.systemone_model == "custom-jev-v2", p.systemone_model)
check("新键已写回配置对象",
      p.config.get("systemone_api_key") == "sk_legacy_1234567890",
      p.config.get("systemone_api_key"))
check("旧键保留不删",
      p.config.get("typesafe_api_key") == "sk_legacy_1234567890",
      p.config.get("typesafe_api_key"))
check("迁移后客户端已就绪", p.systemone_client.is_configured() is True)

# ── 3. 数据目录迁移 ─────────────────────────────────────────────────
check("新数据目录已建立", new_dir.is_dir(), str(new_dir))
check("问答表已迁移到新目录", new_file.exists(), str(new_file))
check("迁移内容与来源一致",
      new_file.exists() and json.loads(new_file.read_text(encoding="utf-8"))["tables"] == fixture_tables)
check("旧数据文件保留", legacy_file.exists(), str(legacy_file))
tables = list(p.qa_store.tables.values())
check("迁移的问答表已装载", sum(len(t.entries) for t in tables) == 2,
      [(t.scope, len(t.entries)) for t in tables])
check("全局表与群表都在", {t.scope for t in tables} == {"global", "group"},
      [t.scope for t in tables])

# ── 4. 新旧键并存：新键优先 ─────────────────────────────────────────
both = dict(LEGACY_CFG)
both["systemone_api_key"] = "sk_new_0000000000"
both["systemone_timeout"] = 5
p2 = M.SystemOneKeyReplyPlugin(ctx, both)
check("新旧 api_key 并存以新键为准", p2.systemone_api_key == "sk_new_0000000000", p2.systemone_api_key)
check("新旧 timeout 并存以新键为准", p2.systemone_timeout == 5, p2.systemone_timeout)

# ── 5. 新目录已有问答表时不被旧目录覆盖 ──────────────────────────────
legacy_file.write_text(
    '{"version": 1, "tables": [{"scope": "global", "scope_id": "", '
    '"entries": [{"question": "改过的旧数据", "answer": {"text": "x"}}]}]}',
    encoding="utf-8")
p3 = M.SystemOneKeyReplyPlugin(ctx, {})
check("已有新数据不被旧目录覆盖",
      json.loads(new_file.read_text(encoding="utf-8"))["tables"] == fixture_tables)
check("覆盖判断不受旧目录影响", p3.qa_store is not None)

# ── 6. 全新安装：既无旧键也无旧目录 ──────────────────────────────────
fresh = ROOT / "fresh"
fresh.mkdir(parents=True)
os.environ["STUB_DATA"] = str(fresh)
p4 = M.SystemOneKeyReplyPlugin(ctx, {})
check("全新安装不报错且种子数据落盘",
      (fresh / "plugin_data" / M.PLUGIN_NAME / "qa_tables.json").exists(),
      str(fresh / "plugin_data" / M.PLUGIN_NAME))
check("全新安装无旧键时 api_key 为空", p4.systemone_api_key == "", p4.systemone_api_key)

print("")
for name, ok, extra in results:
    if not ok:
        print("  FAIL:", name, "->", extra)
passed = sum(1 for _, ok, _ in results if ok)
print(f"PASSED {passed}/{len(results)}")
shutil.rmtree(ROOT, ignore_errors=True)
sys.exit(0 if passed == len(results) else 1)
