# -*- coding: utf-8 -*-
"""图片答案的端到端测试。

回归保护：历史上 table_list 只发送原始 entry（答案在答案池里），
导致页面把纯图片答案显示成「（未填写答案）」，且回复链路把纯图片答案
误判为空答案而静默丢弃。

覆盖：序列化解析答案 / 经典模式发图 / Jev 模式不调用 LLM 直接发图 /
命中测试暴露图片 / 真正的空答案仍静默。

运行（需先准备 astrbot / typesafe_sdk 桩，或用真实环境）：
    PYTHONPATH=<stubs>;. python tests/test_qa_image_answer.py
"""
import sys, os, tempfile, shutil, asyncio, json
from pathlib import Path
ROOT = Path(tempfile.gettempdir()) / "ts_img"
shutil.rmtree(ROOT, ignore_errors=True); ROOT.mkdir(parents=True)
os.environ["STUB_DATA"] = str(ROOT)
import quart
import main as M
from qa_store import SCOPE_GROUP

results = []
def check(n, c, extra=""): results.append((n, bool(c), str(extra)[:130]))

IMG = "https://raw.example.top/a.jpg"

class FakeEvent:
    unified_msg_origin = "g:111"
    def get_sender_id(self): return "u1"
    def get_sender_name(self): return "测试"
    def get_group_id(self): return "111"
    def get_session_id(self): return "111"
    def get_self_id(self): return "bot"
    def get_message_str(self): return ""
    def get_messages(self): return []
    def is_private_chat(self): return False
    def stop_event(self): pass
    def plain_result(self, t): return {"type": "plain", "text": t}
    def chain_result(self, c): return {"type": "chain", "chain": c}
EV = FakeEvent()

cfg = {"reply_source": "固定问答表 (KeyReply)", "enable_reply_delay": False,
       "typesafe_api_key": "ts_key_1234567890", "enable_reply_delay": False}
ctx = type("C", (), {"register_web_api": lambda *a, **k: None,
                     "llm_calls": [],
                     "get_current_chat_provider_id": lambda *a, **k: None})()
p = M.TypeSafeAutoReplyPlugin(ctx, cfg)

async def run():
    # 造两条同图片答案的 Q（会被归组进答案池）
    quart._Req._payload = {"scope": "group", "ids": ["111"], "entries": [
        {"question": "原神", "answer": {"text": "", "images": [IMG]}, "jev": False, "enabled": True},
        {"question": "崩铁", "answer": {"text": "", "images": [IMG]}, "jev": False, "enabled": True},
    ]}
    await p._api_qa_save()

    # 1. 序列化必须带出已解析答案（此前只发原始 answer，页面显示「未填写答案」）
    lst = await p._api_qa_list()
    tbl = [t for t in lst["tables"] if t["key"] == "group:111"][0]
    answers = [e.get("answer") for e in tbl["entries"]]
    check("序列化带出答案文本字段", all(isinstance(a, dict) for a in answers), answers)
    check("序列化带出图片答案", all(a.get("images") == [IMG] for a in answers), answers)
    check("序列化不再丢失 answer_key", all(e.get("answer_key") for e in tbl["entries"]),
          [e.get("answer_key") for e in tbl["entries"]])

    # 2. 纯图片答案必须发出，而不是被判定为空
    cands = p.qa_store.recall_candidates("原神", "group", "111")
    out = []
    async for r in p._qa_send_answer(EV, "原神", "111", cands[0], [], llm_mode=False):
        out.append(r)
    check("纯图片答案不被当作空答案", len(out) == 1, len(out))
    if out:
        chain = out[0]["chain"]
        kinds = [type(c).__name__ for c in chain]
        check("回复链含图片组件", "Image" in kinds, kinds)
        check("回复链不含空的 Plain", not any(
            type(c).__name__ == "Plain" and not getattr(c, "text", "") for c in chain), kinds)

    # 3. Jev 模式：纯图片答案直接发图，不调用 LLM 编造文字
    from classifier import TopicMatch
    p2 = M.TypeSafeAutoReplyPlugin(type("C", (), {"register_web_api": lambda *a, **k: None,
        "llm_calls": [],
        "get_current_provider_id": lambda *a, **k: None})(), cfg)
    p2.qa_store.path = ROOT / "qa2.json"; p2.qa_store.tables = {}
    p2.qa_store.replace_table(SCOPE_GROUP, "111", [
        {"question": "原神", "answer": {"text": "", "images": [IMG]}, "enabled": True}])
    calls = {"llm": 0}
    async def fake_gen(**kw):
        calls["llm"] += 1
        return "不应被调用"
    p2.reply_engine.generate_grounded_reply = fake_gen
    async def fake_topic(state, questions, cache_key_text=None):
        return TopicMatch(matched=True, question="原神", index=0, confidence_score=0.95,
                          confidence_level="high", reason="stub")
    p2.classifier.match_topic = fake_topic
    p2.classifier.client_wrapper.api_key = "ts_x_123456789"
    p2.typesafe_client.api_key = "ts_x_123456789"

    cands2 = p2.qa_store.recall_candidates("原神", "group", "111")
    out2 = []
    async for r in p2._qa_send_answer(EV, "原神", "111", cands2[0], [], llm_mode=True):
        out2.append(r)
    check("纯图片答案在 Jev 模式下也发出", len(out2) == 1, len(out2))
    check("纯图片答案不调用 LLM", calls["llm"] == 0, calls["llm"])
    if out2:
        kinds2 = [type(c).__name__ for c in out2[0]["chain"]]
        check("Jev 模式回复链含图片", "Image" in kinds2, kinds2)

    # 4. 命中测试接口要能看到图片答案
    #    jev=False 的条目走「命中即直接回复」分支，会填充 classic 视图
    quart._Req._payload = {"text": "原神", "scope": "group", "scope_id": "111"}
    t = await p._api_qa_test()
    check("命中测试暴露图片答案", t["classic"] and t["classic"].get("images") == [IMG],
          t.get("classic"))
    check("命中测试报告 would_reply", t.get("would_reply") is True, t.get("would_reply"))

    # 5. 空答案（文本与图片都空）仍应静默
    p3 = M.TypeSafeAutoReplyPlugin(type("C", (), {"register_web_api": lambda *a, **k: None})(), cfg)
    p3.qa_store.path = ROOT / "qa3.json"; p3.qa_store.tables = {}
    p3.qa_store.replace_table(SCOPE_GROUP, "111", [
        {"question": "空答案", "answer": {"text": "", "images": []}, "enabled": True}])
    cands3 = p3.qa_store.recall_candidates("空答案", "group", "111")
    out3 = []
    if cands3:
        async for r in p3._qa_send_answer(EV, "空答案", "111", cands3[0], [], llm_mode=False):
            out3.append(r)
    check("真正空答案仍静默", out3 == [], out3)

asyncio.run(run())
print("")
for n, c, e in results:
    if not c: print("  FAIL:", n, "->", e)
ok = sum(1 for _, c, _ in results if c)
print(f"PASSED {ok}/{len(results)}")
shutil.rmtree(ROOT, ignore_errors=True)
sys.exit(0 if ok == len(results) else 1)