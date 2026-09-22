# -*- coding: utf-8 -*-
"""核心契约测试：正则召回 → Jev 二分类相关性判定 → 回复答案。

用户明确要求的契约：
- Jev 判定前必须先本地正则召回；未命中不调用 Jev
- 命中后把 Q、A 与附加判定增强一起喂给 Jev，让它分辨「真提问」与「假命中」
- 判为假命中则静默；判为真提问则回复该条答案

运行（需先准备 astrbot / typesafe_sdk 桩，或用真实环境）：
    PYTHONPATH=<stubs>;. python tests/test_qa_contract.py
"""
import os, sys, tempfile, shutil, asyncio, json
from pathlib import Path
ROOT = Path(tempfile.gettempdir()) / "ts_contract"
shutil.rmtree(ROOT, ignore_errors=True); ROOT.mkdir(parents=True)
os.environ["STUB_DATA"] = str(ROOT)
import _stubs  # noqa: F401  注入 astrbot / typesafe_sdk 依赖桩
import main as M
from qa_store import SCOPE_GLOBAL
from classifier import TopicMatch

results = []
def check(n, c, extra=""): results.append((n, bool(c), str(extra)[:200]))

cfg = {"enable_jev_topic": True, "typesafe_api_key": "ts_k_1234567890",
       "qa_min_confidence": "中", "enable_reply_delay": False}
ctx = type("C", (), {"register_web_api": lambda *a, **k: None,
                     "llm_generate": None})()

ANSWER = "在本整合包中，金矿石无法在主世界生成，前期最简单的获取方法是烧制贵金属锭，详见任务：矿物处理页面。造出热力离心机后可以离心粉碎铜矿，1/3几率出金。"
HINT = "该回答适合用户询问金锭怎么做的语境"

class FakeEvent:
    unified_msg_origin = "g:1"
    def get_sender_id(self): return "u1"
    def get_sender_name(self): return "测试"
    def get_group_id(self): return "1"
    def get_session_id(self): return "1"
    def get_self_id(self): return "bot"
    def get_message_str(self): return ""
    def get_messages(self): return []
    def is_private_chat(self): return False
    def stop_event(self): pass
    def chain_result(self, c): return {"chain": c}

EV = FakeEvent()

async def run():
    p = M.TypeSafeAutoReplyPlugin(ctx, cfg)
    p.qa_store.tables = {}
    p.qa_store.replace_table(SCOPE_GLOBAL, "", [
        {"question": "金锭", "answer": {"text": ANSWER, "images": []}, "hint": HINT, "enabled": True},
    ])

    # 捕获喂给 Jev 的 criteria
    captured = {}
    async def spy(state, candidates, cache_key_text=None):
        captured["candidates"] = candidates
        captured["state_msg"] = state["current_message"]["text"]
        # 构造真实的 Choice，检查 criteria 是否含 Q/A/hint
        q = p.classifier.build_relevance_question(candidates)
        captured["criteria"] = q.criteria if q else {}
        captured["instructions"] = q.instructions if q else ""
        return TopicMatch(matched=True, question="金锭", index=0,
                          confidence_score=0.92, confidence_level="high", reason="stub")
    p.classifier.match_relevance = spy
    p.enable_jev_topic = True

    # LLM 也会被调用（围绕 A 生成）；桩掉它以便检查提示词
    prompts = []
    async def fake_gen(**kw):
        prompts.append(kw)
        return "围绕 A 生成的回复"
    p.reply_engine.generate_grounded_reply = fake_gen

    out = []
    async for r in p._handle_qa_reply(EV, "金锭怎么做", "1", True, "1", []):
        out.append(r)

    # ① 假命中场景：Jev 一并看到 Q、A、hint
    cand = (captured.get("candidates") or [{}])[0]
    check("Jev 收到问题 Q", cand.get("question") == "金锭", cand.get("question"))
    check("Jev 收到答案 A", cand.get("answer_text") == ANSWER, (cand.get("answer_text") or "")[:40])
    check("Jev 收到附加判定增强", cand.get("hint") == HINT, cand.get("hint"))

    crit = captured.get("criteria") or {}
    check("criteria 为二分类（relevant0 + irrelevant）",
          set(crit.keys()) == {"relevant0", "irrelevant"}, list(crit.keys()))
    check("criteria 含答案 A 文本", ANSWER[:20] in json.dumps(crit, ensure_ascii=False))
    check("criteria 含附加说明", HINT in json.dumps(crit, ensure_ascii=False))
    check("instructions 说明假命中判断", "字面上包含" in (captured.get("instructions") or ""))

    # ② 真提问 → 走 LLM 围绕 A 生成
    check("真提问时回复了", len(out) == 1, len(out))
    # 这里替换的是 ReplyEngine.generate_grounded_reply，因此捕获到的是
    # grounded_answer 参数；真正的提示词由该函数内部用 qa_engine.build_grounded_prompt 组装
    check("围绕生成的素材是答案 A",
          prompts and prompts[0].get("grounded_answer") == ANSWER,
          (prompts[0].get("grounded_answer", "")[:40] if prompts else "no llm call"))
    # 另外直接验证提示词构造函数确实把 A 作为唯一事实来源写入
    from qa_engine import build_grounded_prompt
    sysp, usr = build_grounded_prompt("金锭怎么做", ANSWER, "上下文", "自然", "简短",
                                      max_chars=200, custom_prompt="", image_count=0)
    check("提示词含完整答案 A", ANSWER in usr, usr[:80])
    check("提示词声明 A 是唯一事实来源", "唯一事实来源" in sysp, sysp[:60])

    # ③ 假命中 → 静默，且不调用 LLM
    p2 = M.TypeSafeAutoReplyPlugin(ctx, cfg)
    p2.qa_store.tables = {}
    p2.qa_store.replace_table(SCOPE_GLOBAL, "", [
        {"question": "金锭", "answer": {"text": ANSWER, "images": []}, "hint": HINT, "enabled": True},
    ])
    async def spy_false(state, candidates, cache_key_text=None):
        return TopicMatch(matched=False, question="", index=-1,
                          confidence_score=0.9, confidence_level="high", reason="假命中：只是字面含关键词")
    p2.classifier.match_relevance = spy_false
    llm_calls = []
    async def fake_gen2(**kw):
        llm_calls.append(kw); return "不应出现"
    p2.reply_engine.generate_grounded_reply = fake_gen2
    out2 = []
    async for r in p2._handle_qa_reply(EV, "我买了个金锭形状的钥匙扣", "1", True, "1", []):
        out2.append(r)
    check("假命中时静默", out2 == [], out2)
    check("假命中时不调用 LLM", llm_calls == [], len(llm_calls))

    # ④ 正则未命中 → 完全不触发 Jev
    called = {"n": 0}
    async def spy_none(state, candidates, cache_key_text=None):
        called["n"] += 1
        return TopicMatch(matched=False, question="", index=-1, confidence_score=0,
                          confidence_level="low", reason="x")
    p2.classifier.match_relevance = spy_none
    out3 = []
    async for r in p2._handle_qa_reply(EV, "今天天气真好", "1", True, "1", []):
        out3.append(r)
    check("未召回时不调用 Jev", called["n"] == 0, called["n"])
    check("未召回时静默", out3 == [], out3)

asyncio.run(run())
print("")
for n, c, e in results:
    if not c: print("  FAIL:", n, "->", e)
ok = sum(1 for _, c, _ in results if c)
print(f"PASSED {ok}/{len(results)}")
shutil.rmtree(ROOT, ignore_errors=True)
sys.exit(0 if ok == len(results) else 1)