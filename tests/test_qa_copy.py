# -*- coding: utf-8 -*-
"""一键复制 KeyReply 问答表：端到端测试。

覆盖：来源探测 → 复制到本插件数据目录 → 落盘校验 → 多 Q 一 A 归组 →
合并/覆盖两种模式 → 缺失文件时的可排查错误信息。

运行（需先准备 astrbot / typesafe_sdk 桩，或用真实环境）：
    PYTHONPATH=<stubs>;. python tests/test_qa_copy.py
"""
import sys, os, tempfile, shutil, asyncio, json
from pathlib import Path

ROOT = Path(tempfile.gettempdir()) / "ts_copy"
shutil.rmtree(ROOT, ignore_errors=True); ROOT.mkdir(parents=True)
os.environ["STUB_DATA"] = str(ROOT)

# 造一个真实的 KeyReply 数据文件（含 repr 键、图片、模糊匹配）
KR_DIR = ROOT / "plugins" / "keyword_reply"
KR_DIR.mkdir(parents=True)
KR = KR_DIR / "triggers.yml"
import yaml
yaml.safe_dump({"triggers": {
    "{'text': '怎么安装%', 'images': []}": {"text": "把插件放进 data/plugins 目录", "images": []},
    "{'text': '%如何安装%', 'images': []}": {"text": "把插件放进 data/plugins 目录", "images": []},
    "{'text': '部署教程', 'images': []}": {"text": "把插件放进 data/plugins 目录", "images": []},
    "{'text': '带图的', 'images': []}": {"text": "看图", "images": ["http://x/1.png"]},
}}, open(KR, "w", encoding="utf-8"), allow_unicode=True)

import quart
import main as M
from qa_store import SCOPE_GLOBAL

results = []
def check(n, c, extra=""): results.append((n, bool(c), str(extra)[:130]))

cfg = {"reply_source": "固定问答表 (KeyReply)", "enable_jev_topic": True,
       "typesafe_api_key": "ts_key_1234567890"}
ctx = type("C", (), {"register_web_api": lambda *a, **k: None})()
p = M.TypeSafeAutoReplyPlugin(ctx, cfg)
p.qa_store.path = ROOT / "plugin_data" / "qa_tables.json"
p.qa_store.data_dir = p.qa_store.path.parent
p.qa_store.tables = {}

async def run():
    # 0. 插件自己的数据目录应当独立于 KeyReply
    check("数据目录与 KeyReply 分离", str(KR) not in str(p.qa_store.path), p.qa_store.path)

    # 1. 先让探测能找到（模拟 AstrBot 根目录）—— 直接走显式路径
    quart._Req._payload = {"scope": "global", "scope_id": "", "path": str(KR), "replace": True}
    res = await p._api_qa_import()
    if isinstance(res, tuple): res = res[0]
    check("复制成功", res.get("ok") is True, res.get("message"))
    check("返回来源路径", res.get("source_path") == str(KR), res.get("source_path"))
    check("返回目标路径", "qa_tables.json" in str(res.get("target_path", "")), res.get("target_path"))
    check("复制了 4 条", res.get("imported") == 4, res.get("imported"))

    # 2. 真的写到了本插件自己的目录
    check("目标文件已生成", p.qa_store.path.exists(), p.qa_store.path)
    disk = json.loads(p.qa_store.path.read_text(encoding="utf-8"))
    n_entries = sum(len(t["entries"]) for t in disk["tables"])
    check("落盘条目数正确", n_entries == 4, n_entries)

    # 3. 多 Q 一 A 已归组：按答案文本分组后再断言，避免依赖条目顺序
    #    （yaml.safe_dump 会按键排序，落盘顺序与源文件书写顺序不同）
    tbl = p.qa_store.tables["global"]
    check("答案池只有一组", len(tbl.answers) == 1, list(tbl.answers.keys()))
    install_keys = [e.get("answer_key") for e in tbl.entries
                    if "怎么安装" in e["question"] or "如何安装" in e["question"] or "部署" in e["question"]]
    check("3 条安装类 Q 全部指向同一组", len(install_keys) == 3 and len(set(install_keys)) == 1, install_keys)
    img_keys = [e.get("answer_key") for e in tbl.entries if e["question"] == "带图的"]
    check("不同答案的 Q 不参与分组", img_keys == [None], img_keys)
    check("答案池内容正确", list(tbl.answers.values())[0]["text"].startswith("把插件放进"), list(tbl.answers.values())[0])

    # 4. 合并模式：重复导入应全部跳过
    quart._Req._payload = {"scope": "global", "scope_id": "", "path": str(KR), "replace": False}
    res2 = await p._api_qa_import()
    if isinstance(res2, tuple): res2 = res2[0]
    check("合并模式跳过重复", res2.get("imported") == 0 and res2.get("skipped") == 4, res2)

    # 5. 找不到文件：应返回 searched 列表便于排查
    quart._Req._payload = {"scope": "global", "scope_id": "", "path": str(ROOT / "nope.yml")}
    res3 = await p._api_qa_import()
    status = res3[1] if isinstance(res3, tuple) else 200
    body = res3[0] if isinstance(res3, tuple) else res3
    check("缺文件返回 400", status == 400, status)
    check("缺文件带 searched", isinstance(body.get("searched"), list) and len(body["searched"]) > 0,
          len(body.get("searched") or []))

    # 6. 自动探测：把文件放到探测路径之一
    auto_dir = ROOT / "data" / "plugins" / "keyword_reply"
    auto_dir.mkdir(parents=True)
    shutil.copy(KR, auto_dir / "triggers.yml")
    os.chdir(ROOT)
    found = p.qa_store.find_keyreply_files()
    check("自动探测命中", len(found) > 0, found[:1])

asyncio.run(run())
print("")
for n, c, e in results:
    if not c: print("  FAIL:", n, "->", e)
ok = sum(1 for _, c, _ in results if c)
print(f"PASSED {ok}/{len(results)}")
shutil.rmtree(ROOT, ignore_errors=True)
sys.exit(0 if ok == len(results) else 1)