# -*- coding: utf-8 -*-
"""多群一域（一张问答表服务多个群）与旧数据兼容性测试。

覆盖：多 ID 建表 / 多群命中同一张表 / 按 key 追加群号 / 未配置群回退全局 /
删除保护 / 旧版单 scope_id 数据文件的自动兼容。

运行（需先准备 astrbot / typesafe_sdk 桩，或用真实环境）：
    PYTHONPATH=<stubs>;. python tests/test_qa_multigroup.py
"""
import sys, os, tempfile, shutil, asyncio, json
from pathlib import Path
ROOT = Path(tempfile.gettempdir()) / "ts_multi"
shutil.rmtree(ROOT, ignore_errors=True); ROOT.mkdir(parents=True)
os.environ["STUB_DATA"] = str(ROOT)
import _stubs  # noqa: F401  注入 astrbot / typesafe_sdk / quart 桩
import quart
import main as M
from qa_store import SCOPE_GLOBAL, SCOPE_GROUP

results = []
def check(n, c, extra=""): results.append((n, bool(c), str(extra)[:130]))

cfg = {"reply_source": "固定问答表 (KeyReply)", "enable_jev_topic": False,
       "systemone_api_key": "sk_key_1234567890"}
ctx = type("C", (), {"register_web_api": lambda *a, **k: None})()
p = M.SystemOneKeyReplyPlugin(ctx, cfg)

async def run():
    # 1. 创建一张服务 3 个群的表
    quart._Req._payload = {"scope": "group", "ids": ["111", "222", "333"], "name": "技术群组",
        "entries": [{"question": "群内怎么装%", "answer": {"text": "群内统一答案", "images": []}, "enabled": True}]}
    res = await p._api_qa_save()
    check("保存多群表成功", res.get("message") == "ok", res.get("message"))
    tbl = res.get("table") or {}
    check("表保留 3 个 ID", tbl.get("ids") == ["111", "222", "333"], tbl.get("ids"))
    check("表名已保存", tbl.get("name") == "技术群组", tbl.get("name"))
    check("summary 列出全部群", set(res["summary"]["groups"]) >= {"111", "222", "333"}, res["summary"]["groups"])

    # 2. 三个群都能命中同一张表
    for gid in ["111", "222", "333"]:
        hit = p.qa_store.find_reply("群内怎么装东西", "group", gid)
        check(f"群 {gid} 命中同一张表", bool(hit) and hit["entry"]["answer"]["text"] == "群内统一答案",
              hit and hit.get("table_key"))

    # 3. 只应存在一张群表，而不是三张
    group_tables = [t for t in p.qa_store.tables.values() if t.scope == SCOPE_GROUP]
    check("只有一张群表（多群一域）", len(group_tables) == 1, [t.key for t in group_tables])

    # 4. 追加一个群到已有表（按 key 定位）
    quart._Req._payload = {"key": tbl["key"], "scope": "group", "ids": ["111", "222", "333", "444"],
        "name": "技术群组", "entries": tbl["entries"]}
    res2 = await p._api_qa_save()
    check("追加群号成功", res2.get("table", {}).get("ids") == ["111", "222", "333", "444"], res2.get("table", {}).get("ids"))
    check("追加后仍是同一张表", res2.get("table", {}).get("key") == tbl["key"], res2.get("table", {}).get("key"))
    check("新群可命中", bool(p.qa_store.find_reply("群内怎么装东西", "group", "444")))

    # 5. 未配置的群回退全局表
    p.qa_store.replace_table(SCOPE_GLOBAL, "", [{"question": "全局问题", "answer": {"text": "全局答案"}, "enabled": True}])
    g = p.qa_store.find_reply("全局问题", "group", "999")
    check("未配置群回退全局", bool(g) and g["entry"]["answer"]["text"] == "全局答案", g and g.get("table_key"))

    # 6. 删除该表
    quart._Req._payload = {"key": tbl["key"]}
    d = await p._api_qa_delete()
    check("删除表成功", d.get("message") == "ok", d.get("message"))
    check("删除后不再命中", p.qa_store.find_reply("群内怎么装东西", "group", "111") is None)
    # 全局默认表不允许删除，应返回 400
    quart._Req._payload = {"key": "global"}
    protected = await p._api_qa_delete()
    check("全局表受删除保护", isinstance(protected, tuple) and protected[1] == 400, protected)

    # 7. 旧数据兼容：单 scope_id 的历史文件必须能读
    legacy = ROOT / "legacy" / "qa_tables.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({
        "version": 1,
        "tables": [
            {"scope": "global", "scope_id": "", "entries": [{"question": "旧全局", "answer": {"text": "A"}, "enabled": True}]},
            {"scope": "group", "scope_id": "888", "entries": [{"question": "旧群表", "answer": {"text": "B"}, "enabled": True}]}
        ]
    }, ensure_ascii=False), encoding="utf-8")
    from qa_store import QAStore
    legacy_store = QAStore(legacy.parent)
    lg = legacy_store.find_reply("旧群表", "group", "888")
    check("旧数据可加载并命中", bool(lg), lg and lg.get("table_key"))
    old_tbl = legacy_store.tables.get("group:888")
    check("旧数据 ids 自动补齐", old_tbl is not None and old_tbl.ids == ["888"], old_tbl and old_tbl.ids)

asyncio.run(run())
print("")
for n, c, e in results:
    if not c: print("  FAIL:", n, "->", e)
ok = sum(1 for _, c, _ in results if c)
print(f"PASSED {ok}/{len(results)}")
shutil.rmtree(ROOT, ignore_errors=True)
sys.exit(0 if ok == len(results) else 1)