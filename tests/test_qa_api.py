# -*- coding: utf-8 -*-
"""固定问答表后端 API 测试。

覆盖页面的完整读写闭环：list → save（含多 Q 一 A 归组）→ 回读 → 落盘 → 命中测试。

运行（需先准备 astrbot / typesafe_sdk 桩，或用真实环境）：
    PYTHONPATH=<stubs>;. python tests/test_qa_api.py
本文件不依赖 pytest，直接 python 运行即可。
"""
import sys, os, tempfile, shutil, asyncio, json
from pathlib import Path
ROOT = Path(tempfile.gettempdir()) / "ts_qaapi"
shutil.rmtree(ROOT, ignore_errors=True); ROOT.mkdir(parents=True)
os.environ["STUB_DATA"] = str(ROOT)
import quart
import main as M
from qa_store import SCOPE_GLOBAL, SCOPE_GROUP

results = []
def check(n, c, extra=""): results.append((n, bool(c), str(extra)[:120]))

cfg = {"enable_jev_topic": True,
       "typesafe_api_key": "ts_key_1234567890", "qa_min_confidence": "中"}
ctx = type("C", (), {"register_web_api": lambda *a, **k: None})()
p = M.TypeSafeAutoReplyPlugin(ctx, cfg)

async def run():
    # 1. 页面初次加载：list 应给出全局表与摘要
    lst = await p._api_qa_list()
    check("qa/list 返回 tables", isinstance(lst.get("tables"), list))
    check("qa/list 含 summary", "summary" in lst)
    check("qa/list 含 mode 信息", "mode" in lst and "mode_label" in lst)

    # 2. 保存全局表（页面 collectDraft 的产物形态）
    quart._Req._payload = {
        "scope": "global", "scope_id": "",
        "entries": [
            {"question": "%安装%", "answer": {"text": "统一答案", "images": []}, "enabled": True},
            {"question": "%插件%", "answer": {"text": "统一答案", "images": []}, "enabled": True},
            {"question": "禁用的", "answer": {"text": "x", "images": []}, "enabled": False},
        ],
    }
    res = await p._api_qa_save()
    check("qa/save 成功", res.get("message") == "ok", res)

    # 3. 回读：多 Q 一 A 应已由后端归组
    lst2 = await p._api_qa_list()
    g = [t for t in lst2["tables"] if t["key"] == "global"][0]
    keys = [e.get("answer_key") for e in g["entries"]]
    check("保存后多 Q 已归组", keys[0] and keys[0] == keys[1], keys)
    check("停用项保留 enabled=False", g["entries"][2]["enabled"] is False)

    # 4. 群专属表
    quart._Req._payload = {"scope": "group", "scope_id": "111",
        "entries": [{"question": "群专属", "answer": {"text": "群答案", "images": []}, "enabled": True}]}
    await p._api_qa_save()
    lst3 = await p._api_qa_list()
    check("群表出现在 summary", "111" in lst3["summary"]["groups"], lst3["summary"])

    # 5. 数据确实落到磁盘
    disk = json.loads(p.qa_store.path.read_text(encoding="utf-8"))
    check("数据已写盘", len(disk["tables"]) >= 2, [t["scope"] for t in disk["tables"]])

    # 6. 非法入参
    quart._Req._payload = {"scope": "group", "scope_id": "", "entries": []}
    bad = await p._api_qa_save()
    check("群表缺 scope_id 被拒", isinstance(bad, tuple) and bad[1] == 400, bad)
    quart._Req._payload = {"scope": "bogus", "entries": []}
    bad2 = await p._api_qa_save()
    check("未知作用域被拒", isinstance(bad2, tuple) and bad2[1] == 400)

    # 7. 命中测试接口
    quart._Req._payload = {"text": "怎么安装插件", "scope": "global", "scope_id": ""}
    t = await p._api_qa_test()
    check("qa/test 返回 would_reply", "would_reply" in t, list(t.keys())[:6])
    check("qa/test 报告召回条目", len(t.get("recalled") or []) >= 2, len(t.get("recalled") or []))
    check("qa/test 报告候选数", t.get("candidate_count", 0) >= 2, t.get("candidate_count"))

asyncio.run(run())
print("")
for n, c, e in results:
    if not c: print("  FAIL:", n, "->", e)
ok = sum(1 for _, c, _ in results if c)
print(f"PASSED {ok}/{len(results)}")
shutil.rmtree(ROOT, ignore_errors=True)
sys.exit(0 if ok == len(results) else 1)