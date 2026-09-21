# -*- coding: utf-8 -*-
"""一 A 多 Q：页面产物 → 后端保存 → 回读 → 召回。

验证「答案只写一次、下面挂多个问法」的完整闭环，以及 hint 归属答案级、
每个 Q 都能取到同一份答案与辅助说明。

运行（需先准备 astrbot / typesafe_sdk 桩，或用真实环境）：
    PYTHONPATH=<stubs>;. python tests/test_qa_one_answer.py
"""
import os, sys, tempfile, shutil, asyncio, json
from pathlib import Path
ROOT = Path(tempfile.gettempdir()) / "ts_1a"; shutil.rmtree(ROOT, ignore_errors=True); ROOT.mkdir(parents=True)
os.environ["STUB_DATA"] = str(ROOT)
import quart
import main as M
from qa_store import SCOPE_GLOBAL

results = []
def check(n, c, extra=""): results.append((n, bool(c), str(extra)[:170]))

ANSWER = "在本整合包中，金矿石无法在主世界生成。"
HINT = "该回答适合用户询问金锭怎么做的语境"

async def run():
    p = M.TypeSafeAutoReplyPlugin(type("C", (), {"register_web_api": lambda *a, **k: None})(),
                                  {"enable_jev_topic": True, "typesafe_api_key": "ts_k_1234567890"})

    # 页面 collectDraft 的产物形态：同答案的多条 Q，各带同一份 hint
    quart._Req._payload = {"scope": "global", "scope_id": "", "entries": [
        {"question": "金锭", "answer": {"text": ANSWER, "images": []}, "hint": HINT, "enabled": True},
        {"question": "金锭怎么获得", "answer": {"text": ANSWER, "images": []}, "hint": HINT, "enabled": True},
        {"question": "金锭怎么做", "answer": {"text": ANSWER, "images": []}, "hint": HINT, "enabled": True},
        {"question": "原神", "answer": {"text": "", "images": ["http://x/a.jpg"]}, "enabled": True},
    ]}
    res = await p._api_qa_save()
    check("保存成功", res.get("message") == "ok", res.get("message"))

    tbl = p.qa_store.tables[SCOPE_GLOBAL]
    check("三条 Q 归入同一答案池", len(tbl.answers) == 1, list(tbl.answers.keys()))
    check("答案池携带 hint", list(tbl.answers.values())[0].get("hint") == HINT,
          list(tbl.answers.values())[0])
    keys = [e.get("answer_key") for e in tbl.entries]
    check("三条 Q 指向同一 key", keys[0] and keys[0] == keys[1] == keys[2], keys)

    # 回读：序列化后每个 Q 都能拿到 hint
    lst = await p._api_qa_list()
    g = [t for t in lst["tables"] if t["key"] == "global"][0]
    hints = [e.get("hint") for e in g["entries"][:3]]
    check("回读三条 Q 均带 hint", all(h == HINT for h in hints), hints)

    # 召回：任一问法都能取到答案与 hint
    for msg in ["金锭", "金锭怎么获得", "金锭怎么做"]:
        cands = p.qa_store.recall_candidates(msg, "global", "")
        check(f"召回 {msg}", cands and cands[0]["answer_text"] == ANSWER and cands[0]["hint"] == HINT,
              cands[0] if cands else None)

    # 图片答案仍独立成组
    img_c = p.qa_store.recall_candidates("原神", "global", "")
    check("图片答案独立", img_c and img_c[0]["answer_images"] == ["http://x/a.jpg"], img_c)

asyncio.run(run())
print("")
for n, c, e in results:
    if not c: print("  FAIL:", n, "->", e)
ok = sum(1 for _, c, _ in results if c)
print(f"PASSED {ok}/{len(results)}")
shutil.rmtree(ROOT, ignore_errors=True)
sys.exit(0 if ok == len(results) else 1)