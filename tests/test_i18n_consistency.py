# -*- coding: utf-8 -*-
"""插件展示名与页面名的 i18n 一致性测试。

AstrBot 解析 WebUI 展示名的顺序是：
1. .astrbot-plugin/i18n/<locale>.json 的 metadata 覆盖；
2. 回退 metadata.yaml 的 display_name / short_desc / desc；
3. 再回退机器标识 name——也就是用户看到的 astrbot_plugin_xxx 原始 id。

链条里少一环，插件名与插件页面名就会在 WebUI 里显示成原始 id。本测试守住整条链：

- metadata.yaml 必须提供 display_name / short_desc / desc；
- 每个语言文件的 metadata.display_name / short_desc / desc 均非空；
- i18n 的 pages 段键与 pages/<目录>/index.html 一一对应（双向）；
- _page.json 的 i18n_key 指向的键在每个语言文件里都能解析到；
- 各语言文件的键集合一致，避免新增页面漏翻；
- zh-CN 的 metadata 与 metadata.yaml 完全一致，避免两处漂移。

不依赖 astrbot，可直接运行： python tests/test_i18n_consistency.py
"""

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
METADATA = REPO / "metadata.yaml"
I18N_DIR = REPO / ".astrbot-plugin" / "i18n"
PAGES_DIR = REPO / "pages"

results = []


def check(name, ok, extra=""):
    results.append((name, bool(ok), str(extra)[:200]))


def parse_metadata(text):
    """极简 YAML 顶层键解析：metadata.yaml 是扁平结构，避免引入 PyYAML 依赖。"""
    meta = {}
    for line in text.splitlines():
        if not line or line.startswith(("#", " ", "\t")) or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta


def dig(obj, dotted):
    """按点号路径取值，取不到返回 None。"""
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def page_names():
    """pages/ 下真正会被扫描到的页面目录（必须有 index.html）。"""
    if not PAGES_DIR.is_dir():
        return []
    return sorted(
        p.name for p in PAGES_DIR.iterdir()
        if p.is_dir() and (p / "index.html").is_file()
    )


def main():
    meta = parse_metadata(METADATA.read_text(encoding="utf-8"))

    # ── metadata.yaml：展示名链的第二环 ──────────────────────────────
    for field in ("display_name", "short_desc", "desc"):
        check(f"metadata.yaml 提供 {field}", bool(meta.get(field, "").strip()),
              meta.get(field, ""))
    check("metadata.yaml 的 name 是机器标识（不含中文/空格/连字符）",
          bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", meta.get("name", ""))),
          meta.get("name"))

    # ── 语言文件 ────────────────────────────────────────────────────
    files = sorted(I18N_DIR.glob("*.json"))
    check("存在 i18n 语言文件", len(files) >= 1, [f.name for f in files])
    check("i18n 目录位于 .astrbot-plugin/i18n",
          I18N_DIR.is_dir() and I18N_DIR.parent.name == ".astrbot-plugin",
          str(I18N_DIR.relative_to(REPO)))

    loaded = {}
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # 语法坏了会导致整份翻译静默失效
            check(f"{path.name} 是合法 JSON", False, exc)
            continue
        check(f"{path.name} 是合法 JSON", isinstance(data, dict), type(data).__name__)
        if not isinstance(data, dict):
            continue
        loaded[path.name] = data

        # 顶层键只允许文档约定的 metadata / pages / config
        unknown = set(data) - {"metadata", "pages", "config"}
        check(f"{path.name} 无未知顶层键", not unknown, sorted(unknown))

        md = data.get("metadata")
        check(f"{path.name} 有 metadata 段", isinstance(md, dict), type(md).__name__)
        for field in ("display_name", "short_desc", "desc"):
            value = (md or {}).get(field, "")
            check(f"{path.name} 的 metadata.{field} 非空",
                  isinstance(value, str) and value.strip(), value)

    # ── 页面：目录 ↔ i18n 键 双向对齐 ────────────────────────────────
    dirs = page_names()
    check("扫描到插件页面目录", len(dirs) >= 1, dirs)

    for name, data in loaded.items():
        keys = set((data.get("pages") or {}).keys())
        check(f"{name} 覆盖全部页面目录", not (set(dirs) - keys),
              "缺: " + ", ".join(sorted(set(dirs) - keys)))
        check(f"{name} 无多余页面键", not (keys - set(dirs)),
              "多: " + ", ".join(sorted(keys - set(dirs))))

    # ── _page.json 的 i18n_key 必须能解析 ────────────────────────────
    for dir_name in dirs:
        page_cfg = PAGES_DIR / dir_name / "_page.json"
        check(f"{dir_name} 有 _page.json", page_cfg.is_file(), str(page_cfg))
        if not page_cfg.is_file():
            continue
        try:
            cfg = json.loads(page_cfg.read_text(encoding="utf-8"))
        except Exception as exc:
            check(f"{dir_name}/_page.json 是合法 JSON", False, exc)
            continue
        check(f"{dir_name}/_page.json 是合法 JSON", isinstance(cfg, dict))

        for field in ("title", "description"):
            entry = cfg.get(field)
            check(f"{dir_name}/_page.json 配置了 {field}",
                  isinstance(entry, dict), entry)
            key = (entry or {}).get("i18n_key")
            check(f"{dir_name}/{field} 的 i18n_key 使用 pages.<页面>. 前缀",
                  isinstance(key, str) and key.startswith(f"pages.{dir_name}."), key)
            if not isinstance(key, str):
                continue
            for name, data in loaded.items():
                value = dig(data, key)
                check(f"{name} 能解析 {key}",
                      isinstance(value, str) and value.strip(), value)

    # ── zh-CN 与 metadata.yaml 不得漂移 ─────────────────────────────
    zh = loaded.get("zh-CN.json")
    check("存在 zh-CN.json", isinstance(zh, dict))
    if isinstance(zh, dict):
        zh_md = zh.get("metadata") or {}
        for field in ("display_name", "short_desc", "desc"):
            check(f"zh-CN 的 {field} 与 metadata.yaml 一致",
                  zh_md.get(field) == meta.get(field),
                  (zh_md.get(field), meta.get(field)))

    print("")
    for name, ok, extra in results:
        if not ok:
            print("  FAIL:", name, "->", extra)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"PASSED {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
