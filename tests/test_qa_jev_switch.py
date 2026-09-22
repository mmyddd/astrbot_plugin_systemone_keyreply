# -*- coding: utf-8 -*-
"""条目级 Jev 判定开关测试。

- 问答对默认开启 Jev 判定，除非显式置为 False
- jev=True：正则命中后交由 Jev 判定，真提问才回复（走 LLM 围绕答案生成）
- jev=False：正则命中即直接回复固定答案，不调用 Jev、也不调用 LLM
- 同一消息命中多条时，未开启判定的条目优先
- list 接口需把 jev 透出给页面

运行（需先准备 astrbot / typesafe_sdk 桩，或用真实环境）：
    PYTHONPATH=<stubs>;. python tests/test_qa_jev_switch.py
"""
import os, sys, tempfile, shutil, asyncio
from pathlib import Path
ROOT = Path(tempfile.gettempdir()) / "ts_jev"; shutil.rmtree(ROOT, ignore_errors=True); ROOT.mkdir(parents=True)
os.environ["STUB_DATA"] = str(ROOT)
import _stubs  # noqa: F401  注入 astrbot / typesafe_sdk / quart 桩
import main as M
from qa_store import SCOPE_GLOBAL
from classifier import TopicMatch

results = []
def check(n, c, extra=""): results.append((n, bool(c), str(extra)[:170]))

class E:
    unified_msg_origin = "g:1"
    def get_sender_id(self): return "u"
    def get_sender_name(self): return "t"
    def get_group_id(self): return "1"
    def get_session_id(self): return "1"
    def get_self_id(self): return "b"
    def get_message_str(self): return ""
    def get_messages(self): return []
    def is_private_chat(self): return False
    def stop_event(self): pass
    def chain_result(self, c): return {"chain": c}
EV = E()

def build(entries):
    p = M.SystemOneKeyReplyPlugin(type("C", (), {"register_web_api": lambda *a, **k: None})(),
                                  {"systemone_api_key": "sk_k_1234567890", "enable_reply_delay": False})
    p.qa_store.tables = {}
    p.qa_store.replace_table(SCOPE_GLOBAL, "", entries)
    return p

async def run():
    # 1. 默认开启：normalize 后 jev 为 True
    p = build([{"question": "金锭", "answer": {"text": "答案A"}}])
    tbl = p.qa_store.tables[SCOPE_GLOBAL]
    check("默认 jev=True", tbl.entries[0].get("jev") is True, tbl.entries[0])

    cands = p.qa_store.recall_candidates("金锭", "global", "")
    check("召回透出 jev", cands and cands[0]["jev"] is True, cands)

    # 2. 开启判定的条目必须走 Jev
    called = {"n": 0}
    async def spy(state, candidates, cache_key_text=None):
        called["n"] += 1
        return TopicMatch(matched=True, question="金锭", index=0,
                          confidence_score=0.9, confidence_level="high", reason="s")
    p.classifier.match_relevance = spy
    llm = []
    async def gen(**kw):
        llm.append(kw); return "生成"
    p.reply_engine.generate_grounded_reply = gen
    out = [r async for r in p._handle_qa_reply(EV, "金锭", "1", True, "1", [])]
    check("jev=True 时调用 Jev", called["n"] == 1, called["n"])
    check("jev=True 时回复", len(out) == 1, len(out))
    check("jev=True 时走 LLM", len(llm) == 1, len(llm))

    # 3. 关闭判定的条目：不调用 Jev、不调用 LLM，直接发原答案
    p2 = build([{"question": "金锭", "answer": {"text": "直接答案"}, "jev": False}])
    called2 = {"n": 0}
    async def spy2(state, candidates, cache_key_text=None):
        called2["n"] += 1
        return TopicMatch(matched=False, question="", index=-1, confidence_score=0,
                          confidence_level="low", reason="x")
    p2.classifier.match_relevance = spy2
    llm2 = []
    async def gen2(**kw):
        llm2.append(kw); return "不应生成"
    p2.reply_engine.generate_grounded_reply = gen2
    out2 = [r async for r in p2._handle_qa_reply(EV, "金锭", "1", True, "1", [])]
    check("jev=False 时不调用 Jev", called2["n"] == 0, called2["n"])
    check("jev=False 时不调用 LLM", len(llm2) == 0, len(llm2))
    check("jev=False 时直接发原答案", out2 and out2[0]["chain"][0].text == "直接答案",
          out2 and out2[0]["chain"][0].text)

    # 4. 混合：一条关闭一条开启，关闭优先
    p3 = build([
        {"question": "%金锭%", "answer": {"text": "直接答案"}, "jev": False},
        {"question": "%金锭%", "answer": {"text": "判定答案"}, "jev": True},
    ])
    called3 = {"n": 0}
    async def spy3(state, candidates, cache_key_text=None):
        called3["n"] += 1
        return TopicMatch(matched=True, question=candidates[0]["question"], index=0,
                          confidence_score=0.9, confidence_level="high", reason="s")
    p3.classifier.match_relevance = spy3
    out3 = [r async for r in p3._handle_qa_reply(EV, "金锭", "1", True, "1", [])]
    check("混合时未判定条目优先", out3 and out3[0]["chain"][0].text == "直接答案",
          out3 and out3[0]["chain"][0].text)
    check("混合时不调用 Jev", called3["n"] == 0, called3["n"])

    # 5. 序列化透出 jev（页面回读需要）
    lst = await p3._api_qa_list()
    g = [t for t in lst["tables"] if t["key"] == "global"][0]
    jevs = [e.get("jev") for e in g["entries"]]
    check("回读透出 jev", jevs == [False, True], jevs)

asyncio.run(run())
print("")
for n, c, e in results:
    if not c: print("  FAIL:", n, "->", e)
ok = sum(1 for _, c, _ in results if c)
print(f"PASSED {ok}/{len(results)}")
shutil.rmtree(ROOT, ignore_errors=True)
sys.exit(0 if ok == len(results) else 1)