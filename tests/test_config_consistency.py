# -*- coding: utf-8 -*-
"""配置三方一致性测试。

历史上多次出现「schema 删了但 WebUI 忘删」或「加了 schema 键但没加界面字段」
的遗漏。本测试把 _conf_schema.json、WebUI 字段定义、main.py 的可编辑白名单
三者对齐检查，任何一处不同步都会失败。

不依赖 astrbot，可直接运行： python tests/test_config_consistency.py
"""

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCHEMA = REPO / "_conf_schema.json"
CONFIG_JS = REPO / "pages" / "systemone-console" / "js" / "config.js"
MAIN_PY = REPO / "main.py"
METADATA = REPO / "metadata.yaml"

results = []


def check(name, ok, extra=""):
    results.append((name, bool(ok), str(extra)[:200]))


def main():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    schema_keys = set(schema.keys())
    cfg_js = CONFIG_JS.read_text(encoding="utf-8")
    main_py = MAIN_PY.read_text(encoding="utf-8")

    ui_keys = set(re.findall(r"key: '([a-z_]+)'", cfg_js))
    check("WebUI 覆盖全部 schema 键", not (schema_keys - ui_keys),
          "缺: " + ", ".join(sorted(schema_keys - ui_keys)))
    check("WebUI 无多余字段", not (ui_keys - schema_keys),
          "多: " + ", ".join(sorted(ui_keys - schema_keys)))

    editable = set()
    block_pat = re.compile(r"(_EDITABLE_[A-Z_]+_FIELDS)\s*=\s*\((.*?)\n\)", re.S)
    blocks = block_pat.findall(main_py)
    check("找到全部白名单块", len(blocks) == 4, len(blocks))
    for _name, body in blocks:
        editable |= set(re.findall(r"\"([a-z_]+)\"", body))
    check("白名单覆盖全部 schema 键", not (schema_keys - editable),
          "缺: " + ", ".join(sorted(schema_keys - editable)))
    check("白名单无失效键", not (editable - schema_keys),
          "多: " + ", ".join(sorted(editable - schema_keys)))

    removed = ["force_reply_mode", "force_reply_keywords", "ignore_keywords",
               "force_trigger_regex", "ignore_regex", "normalize_force_reply_mode"]
    files = {
        "schema": json.dumps(schema, ensure_ascii=False),
        "config.js": cfg_js,
        "main.py": main_py,
        "filters.py": (REPO / "filters.py").read_text(encoding="utf-8"),
        "utils.py": (REPO / "utils.py").read_text(encoding="utf-8"),
    }
    for key in removed:
        hit = [f for f, text in files.items() if key in text]
        check(f"已移除的 {key} 未复活", not hit, "出现在: " + ", ".join(hit))

    filters_py = files["filters.py"]
    for helper in ("matches_keywords", "matches_regex"):
        check(f"filters.py 已移除 {helper}", helper not in filters_py)

    # 插件简介与版本号：metadata.yaml 是市场展示的唯一来源，main.py 的 @register
    # 必须与它完全同步，否则市场页与实际行为会各说各话。
    meta = METADATA.read_text(encoding="utf-8")
    desc = (
        "固定问答表驱动的自动关键词回复插件。消息先经本地正则召回，"
        "命中后由 TypeSafe AI 的 SystemOne 判定是否真提问，再回复标准答案。"
    )
    check("metadata 简介与约定一致", ("desc: " + desc) in meta,
          meta.strip().splitlines()[1] if "\n" in meta else meta)
    check("main.py 注册简介与 metadata 一致", desc in main_py)
    meta_version = re.search(r"^version:\s*v?([\d.]+)", meta, re.M)
    code_version = re.search(r'PLUGIN_VERSION = "([\d.]+)"', main_py)
    check("插件版本号两处一致",
          bool(meta_version) and bool(code_version)
          and meta_version.group(1) == code_version.group(1),
          (meta_version and meta_version.group(1), code_version and code_version.group(1)))

    print("")
    for name, ok, extra in results:
        if not ok:
            print("  FAIL:", name, "->", extra)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"PASSED {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
