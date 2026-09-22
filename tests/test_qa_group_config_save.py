# -*- coding: utf-8 -*-
"""群配置保存不得清空问答对（线上事故回归测试）。

事故形态：在「群配置」弹窗里给一张已有群表新增群号并保存后，该表的问答对
全部消失（answers 池残留）。原因是页面把「当前选中的表」的问答对一起提交了，
而弹窗并不会切换选中表——默认选中的全局表往往没有问答对，后端按整体替换语义
写入，于是被编辑的群表被清空。

本测试守住修复后的契约：
- 省略 entries：只更新群号与名称，问答对与答案池原样保留，并落盘；
- 空列表覆盖非空表：被拒绝（400），内存与磁盘都保持不变；
- 显式 allow_empty=true：允许清空（编辑页删除全部问答对的合法路径）；
- 带 entries 的正常保存：仍然整体替换；
- 新建群配置（省略 entries）：得到空表，且不误伤其它表；
- entries 类型非法：明确报错，不静默清空。

运行：python tests/test_qa_group_config_save.py
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(tempfile.gettempdir()) / "ts_group_cfg"
shutil.rmtree(ROOT, ignore_errors=True)
ROOT.mkdir(parents=True)
os.environ["STUB_DATA"] = str(ROOT)

GROUP_A = "198357092"
GROUP_B = "915521426"
GROUP_NEW = "838116737"

# 事故现场：全局表没有问答对，群表有 30 条问答对 + 7 组答案池
ENTRIES = [
    {
        "question": "问题%d" % i,
        "answer": {"text": "", "images": []},
        "answer_key": "a%d" % (i % 7),
        "enabled": True,
    }
    for i in range(30)
]
ANSWERS = {"a%d" % i: {"text": "答案%d" % i, "images": []} for i in range(7)}
FIXTURE = {
    "version": 1,
    "updated_at": "2026-09-22 01:53:06",
    "tables": [
        {
            "scope": "global", "scope_id": "", "ids": [], "name": "",
            "answers": {"a0": {"text": "全局答案", "images": []}}, "entries": [],
        },
        {
            "scope": "group", "scope_id": GROUP_A, "ids": [GROUP_A, GROUP_B],
            "name": "CTNH", "answers": ANSWERS, "entries": ENTRIES,
        },
    ],
}

import _stubs  # noqa: F401  注入 astrbot / typesafe_sdk / quart 桩
import quart
import main as M

DATA_DIR = ROOT / "plugin_data" / M.PLUGIN_NAME
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATA_FILE = DATA_DIR / "qa_tables.json"
DATA_FILE.write_text(json.dumps(FIXTURE, ensure_ascii=False), encoding="utf-8")

ctx = type("C", (), {"register_web_api": lambda *a, **k: None})()
p = M.SystemOneKeyReplyPlugin(ctx, {})

results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond), str(extra)[:200]))


def group_table():
    return p.qa_store.get_table_by_key("group:%s" % GROUP_A)


def disk_group_table():
    tables = json.loads(DATA_FILE.read_text(encoding="utf-8"))["tables"]
    for t in tables:
        if t.get("scope") == "group":
            return t
    return {}


def body_of(res):
    return res[0] if isinstance(res, tuple) else res


def status_of(res):
    return res[1] if isinstance(res, tuple) else 200


async def save(payload):
    quart._Req._payload = payload
    return await p._api_qa_save()


async def run():
    # ── 0. 现场还原 ────────────────────────────────────────────────
    check("群表初始 30 条问答对", len(group_table().entries) == 30, len(group_table().entries))
    check("全局表初始没有问答对",
          len(p.qa_store.get_table_by_key("global").entries) == 0,
          len(p.qa_store.get_table_by_key("global").entries))

    # ── 1. 事故路径：弹窗只改群号（省略 entries）───────────────────
    res = await save({
        "key": "group:%s" % GROUP_A, "scope": "group",
        "ids": [GROUP_A, GROUP_B, GROUP_NEW], "name": "CTNH",
    })
    check("保存成功", body_of(res).get("message") == "ok", body_of(res))
    check("问答对未被清空", len(group_table().entries) == 30, len(group_table().entries))
    check("群号已更新为 3 个",
          list(group_table().ids) == [GROUP_A, GROUP_B, GROUP_NEW], group_table().ids)
    check("答案池保留", len(group_table().answers) == 7, len(group_table().answers))
    check("落盘问答对完整", len(disk_group_table().get("entries") or []) == 30,
          len(disk_group_table().get("entries") or []))
    check("落盘群号完整", disk_group_table().get("ids") == [GROUP_A, GROUP_B, GROUP_NEW],
          disk_group_table().get("ids"))
    check("全局表未受影响",
          len(p.qa_store.get_table_by_key("global").entries) == 0)
    check("回显仍带完整问答对",
          len(body_of(res).get("table", {}).get("entries") or []) == 30,
          len(body_of(res).get("table", {}).get("entries") or []))

    # ── 2. 省略 entries 时同样可以改名 ─────────────────────────────
    res = await save({
        "key": "group:%s" % GROUP_A, "scope": "group",
        "ids": [GROUP_A, GROUP_B, GROUP_NEW], "name": "CTNH 重命名",
    })
    check("省略 entries 时可改名称", group_table().name == "CTNH 重命名", group_table().name)
    check("改名后问答对仍在", len(group_table().entries) == 30, len(group_table().entries))

    # ── 3. 空列表覆盖非空表：拒绝，且内存与磁盘都不变 ───────────────
    res = await save({
        "key": "group:%s" % GROUP_A, "scope": "group",
        "ids": [GROUP_A, GROUP_B, GROUP_NEW], "entries": [],
    })
    check("空列表覆盖被拒绝", status_of(res) == 400, status_of(res))
    check("拒绝信息说明原因", "阻止" in str(body_of(res).get("message")),
          body_of(res).get("message"))
    check("拒绝后内存问答对仍在", len(group_table().entries) == 30, len(group_table().entries))
    check("拒绝后落盘问答对仍在", len(disk_group_table().get("entries") or []) == 30)

    # ── 4. 显式 allow_empty：编辑页清空全部问答对的合法路径 ─────────
    res = await save({
        "key": "group:%s" % GROUP_A, "scope": "group",
        "ids": [GROUP_A, GROUP_B, GROUP_NEW], "entries": [], "allow_empty": True,
    })
    check("显式允许后可清空", len(group_table().entries) == 0, len(group_table().entries))
    check("清空后落盘一致", len(disk_group_table().get("entries") or []) == 0)

    # ── 5. 带 entries 的正常保存：整体替换 ────────────────────────
    res = await save({
        "key": "group:%s" % GROUP_A, "scope": "group",
        "ids": [GROUP_A, GROUP_B, GROUP_NEW],
        "entries": [{
            "question": "唯一问题",
            "answer": {"text": "唯一答案", "images": []},
            "enabled": True,
        }],
    })
    check("带 entries 时整体替换", len(group_table().entries) == 1, len(group_table().entries))
    check("替换后问题正确", group_table().entries[0]["question"] == "唯一问题",
          group_table().entries[0].get("question"))

    # ── 6. 新建群配置（省略 entries）：新表为空且不动已有表 ─────────
    res = await save({"scope": "group", "ids": ["777"], "name": "新群"})
    new_table = p.qa_store.get_table_by_key("group:777")
    check("新建群配置成功", new_table is not None)
    check("新表问答对为空", new_table is not None and len(new_table.entries) == 0,
          None if new_table is None else len(new_table.entries))
    check("已有表未被新表影响", len(group_table().entries) == 1, len(group_table().entries))

    # ── 7. entries 类型非法：报错而不是静默清空 ─────────────────────
    res = await save({
        "key": "group:%s" % GROUP_A, "scope": "group",
        "ids": [GROUP_A], "entries": "oops",
    })
    check("非法 entries 被拒绝", status_of(res) == 400, status_of(res))
    check("非法 entries 后问答对仍在", len(group_table().entries) == 1, len(group_table().entries))


asyncio.run(run())

print("")
for name, ok, extra in results:
    if not ok:
        print("  FAIL:", name, "->", extra)
passed = sum(1 for _, ok, _ in results if ok)
print("PASSED %d/%d" % (passed, len(results)))
shutil.rmtree(ROOT, ignore_errors=True)
sys.exit(0 if passed == len(results) else 1)
