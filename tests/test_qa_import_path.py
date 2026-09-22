# -*- coding: utf-8 -*-
"""KeyReply 导入流程测试（不再依赖插件配置中的路径字段）。

覆盖：实例无 qa_import_path 属性 / list 不再返回该字段 /
显式路径导入 / 不传路径时自动探测 / 旧配置残留键不影响启动。

运行（需先准备 astrbot / typesafe_sdk 桩，或用真实环境）：
    PYTHONPATH=<stubs>;. python tests/test_qa_import_path.py
"""
import sys, os, tempfile, shutil, asyncio
from pathlib import Path
ROOT = Path(tempfile.gettempdir()) / "ts_noimp"
shutil.rmtree(ROOT, ignore_errors=True); ROOT.mkdir(parents=True)
os.environ["STUB_DATA"] = str(ROOT)

# 造一个 KeyReply 数据文件
KR = ROOT / "data" / "plugins" / "keyword_reply"
KR.mkdir(parents=True)
import yaml
yaml.safe_dump({"triggers": {"{'text': '怎么装%', 'images': []}": {"text": "答案", "images": []}}},
               open(KR / "triggers.yml", "w", encoding="utf-8"), allow_unicode=True)

import _stubs  # noqa: F401  注入 astrbot / typesafe_sdk / quart 桩
import quart
import main as M

results = []
def check(n, c, extra=""): results.append((n, bool(c), str(extra)[:130]))

cfg = {"reply_source": "固定问答表 (KeyReply)", "typesafe_api_key": "ts_k_1234567890"}
ctx = type("C", (), {"register_web_api": lambda *a, **k: None})()
p = M.TypeSafeAutoReplyPlugin(ctx, cfg)

async def run():
    # 1. 插件不再持有该属性
    check("实例上已无 qa_import_path", not hasattr(p, "qa_import_path"))

    # 2. list 不再返回 configured_import_path
    lst = await p._api_qa_list()
    check("list 不再返回 configured_import_path", "configured_import_path" not in lst, list(lst.keys()))
    check("list 仍返回 import_candidates", "import_candidates" in lst)

    # 3. 显式传路径仍可导入（页面「手动指定路径」的用法）
    quart._Req._payload = {"scope": "global", "scope_id": "", "path": str(KR / "triggers.yml"), "replace": True}
    res = await p._api_qa_import()
    if isinstance(res, tuple): res = res[0]
    check("显式路径可导入", res.get("ok") is True, res.get("message"))

    # 4. 不传路径时自动探测（一键按钮的用法，cwd 已在 data 父目录）
    os.chdir(ROOT)
    p2 = M.TypeSafeAutoReplyPlugin(ctx, cfg)
    quart._Req._payload = {"scope": "global", "scope_id": "", "replace": True}
    res2 = await p2._api_qa_import()
    if isinstance(res2, tuple): res2 = res2[0]
    check("不传路径时自动探测成功", res2.get("ok") is True, res2.get("message"))

    # 5. 配置里残留的旧键不应导致崩溃
    p3 = M.TypeSafeAutoReplyPlugin(ctx, dict(cfg, qa_import_path="/some/old/path.yml"))
    check("旧配置残留键不影响启动", p3 is not None)

asyncio.run(run())
print("")
for n, c, e in results:
    if not c: print("  FAIL:", n, "->", e)
ok = sum(1 for _, c, _ in results if c)
print(f"PASSED {ok}/{len(results)}")
shutil.rmtree(ROOT, ignore_errors=True)
sys.exit(0 if ok == len(results) else 1)