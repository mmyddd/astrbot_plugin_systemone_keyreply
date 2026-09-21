# -*- coding: utf-8 -*-
"""固定问答表（KeyReply 兼容）的数据模型与持久化。

设计要点：
- 数据存放于 AstrBot 标准插件数据目录 data/plugin_data/<plugin>/ 下，使用 JSON，
  避免 KeyReply 早期的 repr+eval 格式带来的解析风险。
- 支持「全局默认表」+「按群独立表」+「按私聊独立表」三级作用域：
  先查作用域专属表，未命中再回退到全局默认表。
- 兼容 KeyReply 的模糊匹配语义：问题中的 % 表示任意字符（等同于正则 .*）。
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger("astrbot")

# 作用域类型
SCOPE_GLOBAL = "global"
SCOPE_GROUP = "group"
SCOPE_PRIVATE = "private"

SCOPE_LABELS = {
    SCOPE_GLOBAL: "全局默认表",
    SCOPE_GROUP: "群聊专属表",
    SCOPE_PRIVATE: "私聊专属表",
}

DATA_FILE_NAME = "qa_tables.json"
DATA_VERSION = 1

# KeyReply 的模糊匹配符：% 代表任意字符
WILDCARD = "%"


def compile_question_pattern(text: str) -> Optional[re.Pattern]:
    """把 KeyReply 风格的问题文本编译为正则。

    与 KeyReply 一致：% 代表任意字符序列；其余字符按字面量匹配（转义正则元字符），
    因此问题文本里的 ( ) [ ] . * 等符号不会意外变成正则语法。
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    parts = [re.escape(seg) for seg in raw.split(WILDCARD)]
    pattern = ".*".join(parts)
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        logger.warning(f"[TypeSafe][QA] 问题模式编译失败 ({raw}): {e}")
        return None


def matches_question(text: str, message: str) -> bool:
    """判断消息是否命中某个问题模式。"""
    pattern = compile_question_pattern(text)
    if not pattern:
        return False
    return pattern.search(str(message or "")) is not None


def normalize_answer(raw: Any) -> Dict[str, Any]:
    """归一化一条答案：文本 + 图片 URL 列表。"""
    if isinstance(raw, str):
        return {"text": raw, "images": []}
    if isinstance(raw, dict):
        images = raw.get("images") or []
        if isinstance(images, str):
            images = [images]
        return {
            "text": str(raw.get("text") or ""),
            "images": [str(x).strip() for x in images if str(x).strip()],
        }
    return {"text": "", "images": []}


def regroup_answers(table: "QATable", prefix: str = "a") -> None:
    """把表中答案相同的条目归入同一个答案池键（多个 Q → 一个 A）。

    只在答案被至少两条 Q 引用时才建立分组键，单条 Q 保持内联答案，
    这样既支持「多 Q 一 A」，也不会让简单数据变得难以阅读。
    """
    from collections import defaultdict

    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for entry in table.entries:
        key = str(entry.get("answer_key") or "").strip()
        if key:
            buckets[key].append(entry)
            continue
        sig = table.answer_signature(entry)
        if sig.strip("\u0000"):
            buckets[f"__sig__{sig}"].append(entry)

    groups = {k: v for k, v in buckets.items() if len(v) > 1}
    if not groups:
        return

    # 保留既有键名，为新分组分配稳定键
    existing = set(table.answers.keys())
    new_answers: Dict[str, Dict[str, Any]] = {}
    counter = 1
    for bucket_key, members in groups.items():
        if bucket_key.startswith("__sig__"):
            while f"{prefix}{counter}" in existing:
                counter += 1
            key = f"{prefix}{counter}"
            counter += 1
        else:
            key = bucket_key
        new_answers[key] = table.resolve_answer(members[0])
        for entry in members:
            entry["answer_key"] = key

    # 丢弃不再被引用的旧分组键
    table.answers = new_answers


def normalize_entry(raw: Any) -> Optional[Dict[str, Any]]:
    """归一化一条问答对为 {question, answer:{text,images}, enabled}。"""
    if not isinstance(raw, dict):
        return None
    question = str(raw.get("question") or raw.get("text") or "").strip()
    if not question:
        return None
    answer = normalize_answer(raw.get("answer"))
    answer_key = str(raw.get("answer_key") or "").strip()
    entry: Dict[str, Any] = {
        "question": question,
        "answer": answer,
        "enabled": bool(raw.get("enabled", True)),
    }
    # 仅在有分组时写出 answer_key，保持与旧数据的存储形态一致
    if answer_key:
        entry["answer_key"] = answer_key
    return entry


class QATable:
    """单个作用域下的问答表。

    支持两种答案组织方式，可混用：
    - **答案池**：表级 answers 字典，条目用 answer_key 引用，由此实现「多个 Q 指向同一个 A」。
    - **内联答案**：条目自带 answer 字段（KeyReply 兼容的简单形式）。
    """

    def __init__(
        self,
        scope: str = SCOPE_GLOBAL,
        scope_id: str = "",
        entries: Optional[List[dict]] = None,
        answers: Optional[Dict[str, Any]] = None,
        ids: Optional[Iterable[str]] = None,
        name: str = "",
    ):
        self.scope = scope
        # 一张表可服务多个会话 ID（多群一域）；scope_id 保留为首个 ID 以兼容旧数据
        self.ids: List[str] = []
        for raw in (ids if ids is not None else ([scope_id] if scope_id else [])):
            text = str(raw).strip()
            if text and text not in self.ids:
                self.ids.append(text)
        self.scope_id = self.ids[0] if self.ids else ""
        self.name = str(name or "").strip()
        self.answers: Dict[str, Dict[str, Any]] = {}
        if isinstance(answers, dict):
            for key, raw in answers.items():
                k = str(key).strip()
                if k:
                    self.answers[k] = normalize_answer(raw)
        self.entries: List[Dict[str, Any]] = []
        if entries:
            for item in entries:
                norm = normalize_entry(item)
                if norm:
                    self.entries.append(norm)

    # ── 答案解析（多 Q → 一 A 的核心）────────────────────
    def resolve_answer(self, entry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """解析某条目最终生效的答案。

        优先使用 answer_key 指向的答案池条目；未设置或指向不存在时退回内联 answer。
        """
        if not entry:
            return {"text": "", "images": []}
        key = str(entry.get("answer_key") or "").strip()
        if key:
            pooled = self.answers.get(key)
            if pooled is not None:
                return pooled
        return normalize_answer(entry.get("answer"))

    def answer_signature(self, entry: Optional[Dict[str, Any]]) -> str:
        """答案指纹，用于判断多条 Q 是否其实指向同一个 A。"""
        ans = self.resolve_answer(entry)
        return f"{ans.get('text', '')}\u0000{'|'.join(ans.get('images') or [])}"

    def groups(self) -> List[Dict[str, Any]]:
        """列出答案池中的分组及其引用的问题，供 WebUI 展示。"""
        members: Dict[str, List[str]] = {k: [] for k in self.answers}
        for entry in self.entries:
            key = str(entry.get("answer_key") or "").strip()
            if key and key in members:
                members[key].append(str(entry.get("question") or ""))
        return [
            {
                "key": key,
                "answer": self.answers[key],
                "questions": members.get(key, []),
                "count": len(members.get(key, [])),
            }
            for key in self.answers
        ]

    @property
    def key(self) -> str:
        """存储主键：沿用历史形态（scope 或 scope:首个ID），保证既有数据文件可直接读取。"""
        return f"{self.scope}:{self.scope_id}" if self.scope_id else self.scope

    @property
    def label(self) -> str:
        if self.scope == SCOPE_GLOBAL:
            return SCOPE_LABELS[SCOPE_GLOBAL]
        if self.name:
            return self.name
        base = SCOPE_LABELS.get(self.scope, self.scope)
        if len(self.ids) > 1:
            return f"{base} · {self.ids[0]} 等 {len(self.ids)} 个"
        return f"{base} · {self.scope_id}" if self.scope_id else base

    @property
    def id_count(self) -> int:
        return len(self.ids)

    def serves(self, scope_id: str) -> bool:
        """该表是否服务于给定会话 ID。"""
        return str(scope_id or "") in self.ids

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scope": self.scope,
            "scope_id": self.scope_id,
            "ids": list(self.ids),
            "name": self.name,
            "answers": self.answers,
            "entries": self.entries,
        }

    def find_match(self, message: str) -> Optional[Dict[str, Any]]:
        """返回第一条命中的问答对（跳过已停用项）。"""
        for entry in self.entries:
            if not entry.get("enabled", True):
                continue
            if matches_question(entry.get("question", ""), message):
                return entry
        return None

    def question_texts(self) -> List[str]:
        return [e.get("question", "") for e in self.entries if str(e.get("question") or "").strip()]


class QAStore:
    """问答表集合的加载、查询与持久化。"""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / DATA_FILE_NAME
        self.tables: Dict[str, QATable] = {}
        self._ensure_dir()
        self.load()

    # ── 持久化 ────────────────────────────────────────────
    def _ensure_dir(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"[TypeSafe][QA] 创建数据目录失败 {self.data_dir}: {e}")

    def load(self) -> None:
        """从磁盘加载；文件不存在或损坏时退回空表集。"""
        self.tables = {}
        if not self.path.exists():
            self._seed_examples()
            self.save()
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception as e:
            logger.error(f"[TypeSafe][QA] 读取 {self.path} 失败，将以空表启动: {e}")
            self._seed_examples()
            return

        raw_tables = payload.get("tables") if isinstance(payload, dict) else None
        if isinstance(raw_tables, list):
            for item in raw_tables:
                table = self._table_from_dict(item)
                if table:
                    self.tables[table.key] = table
        if not self.tables:
            self._seed_examples()

    def _table_from_dict(self, item: Any) -> Optional[QATable]:
        if not isinstance(item, dict):
            return None
        scope = str(item.get("scope") or SCOPE_GLOBAL)
        if scope not in (SCOPE_GLOBAL, SCOPE_GROUP, SCOPE_PRIVATE):
            scope = SCOPE_GLOBAL
        scope_id = str(item.get("scope_id") or "")
        if scope == SCOPE_GLOBAL:
            scope_id = ""
        # 兼容旧的单 ID 形态：没有 ids 字段时用 scope_id 构造
        ids = item.get("ids")
        if not isinstance(ids, list):
            ids = [scope_id] if scope_id else []
        return QATable(
            scope, scope_id, item.get("entries"), item.get("answers"),
            ids=ids, name=str(item.get("name") or ""),
        )

    def _seed_examples(self) -> None:
        """首次运行时写入一条示例，让页面不至于空白。"""
        example = QATable(SCOPE_GLOBAL, "", [
            {
                "question": "怎么安装%",
                "answer": {
                    "text": "把插件目录放进 AstrBot 的 data/plugins/ 后重启即可，依赖会自动安装。",
                    "images": [],
                },
                "enabled": True,
            }
        ])
        self.tables[example.key] = example

    def save(self) -> bool:
        self._ensure_dir()
        payload = {
            "version": DATA_VERSION,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "tables": [t.to_dict() for t in self.tables.values()],
        }
        try:
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            tmp.replace(self.path)
            return True
        except Exception as e:
            logger.error(f"[TypeSafe][QA] 保存 {self.path} 失败: {e}")
            return False

    # ── 查询 ──────────────────────────────────────────────
    def get_table(self, scope: str, scope_id: str = "") -> QATable:
        """取得（必要时创建）指定作用域的表。

        若已存在服务该 ID 的表则直接复用，避免多群一域被拆成多张表。
        """
        if scope == SCOPE_GLOBAL:
            scope_id = ""
        if scope_id:
            existing = self.find_table_by_id(scope, scope_id)
            if existing is not None:
                return existing
        key = f"{scope}:{scope_id}" if scope_id else scope
        table = self.tables.get(key)
        if table is None:
            table = QATable(scope, scope_id, [])
            self.tables[key] = table
        return table

    def has_table(self, scope: str, scope_id: str = "") -> bool:
        if scope == SCOPE_GLOBAL:
            return SCOPE_GLOBAL in self.tables
        if not scope_id:
            return False
        return self.find_table_by_id(scope, scope_id) is not None

    def get_table_by_key(self, key: str) -> Optional[QATable]:
        """按存储主键取表（页面用它精确指向某一张表）。"""
        return self.tables.get(str(key or ""))

    def _candidate_tables(self, scope: str, scope_id: str = "") -> List["QATable"]:
        """按优先级排列候选表：命中的专属表在前，全局默认表兜底。

        专属表按 ids 匹配，因此一张表可以同时服务多个群（多群一域）。
        """
        ordered: List[QATable] = []
        if scope in (SCOPE_GROUP, SCOPE_PRIVATE) and scope_id:
            for table in self.tables.values():
                if table.scope == scope and table.serves(scope_id):
                    ordered.append(table)
        global_table = self.tables.get(SCOPE_GLOBAL)
        if global_table is not None:
            ordered.append(global_table)
        return ordered

    def find_table_by_id(self, scope: str, scope_id: str) -> Optional["QATable"]:
        """按会话 ID 找到服务于它的专属表。"""
        target = str(scope_id or "").strip()
        if not target:
            return None
        for table in self.tables.values():
            if table.scope == scope and table.serves(target):
                return table
        return None

    def find_all_replies(self, message: str, scope: str, scope_id: str = "") -> List[Dict[str, Any]]:
        """Regex recall: return every entry whose question pattern matches the message.

        Ordered by table priority (own table first, then global); within a table the
        declaration order is preserved so the first entry stays the best default.
        """
        hits: List[Dict[str, Any]] = []
        for table in self._candidate_tables(scope, scope_id):
            for entry in table.entries:
                if not entry.get("enabled", True):
                    continue
                if matches_question(entry.get("question", ""), message):
                    hits.append({
                        "entry": entry,
                        "answer": table.resolve_answer(entry),
                        "scope": table.scope,
                        "scope_id": table.scope_id,
                        "table_key": table.key,
                        "table_label": table.label,
                    })
        return hits

    def find_reply_by_question(self, question: str, scope: str, scope_id: str = "") -> Optional[Dict[str, Any]]:
        """按问题原文精确取回问答对（Jev 语义路由命中后调用）。"""
        target = str(question or "").strip()
        if not target:
            return None
        for table in self._candidate_tables(scope, scope_id):
            for entry in table.entries:
                if str(entry.get("question") or "").strip() == target:
                    return {
                        "entry": entry,
                        "answer": table.resolve_answer(entry),
                        "scope": table.scope,
                        "scope_id": table.scope_id,
                        "table_key": table.key,
                        "table_label": table.label,
                    }
        return None

    def find_reply(self, message: str, scope: str, scope_id: str = "") -> Optional[Dict[str, Any]]:
        """在专属表与全局表上依次查找命中项。

        返回 {entry, scope, scope_id, table_key} 或 None。
        """
        for table in self._candidate_tables(scope, scope_id):
            entry = table.find_match(message)
            if entry:
                return {
                    "entry": entry,
                    "answer": table.resolve_answer(entry),
                    "scope": table.scope,
                    "scope_id": table.scope_id,
                    "table_key": table.key,
                    "table_label": table.label,
                }
        return None

    def candidate_questions(self, scope: str, scope_id: str = "") -> List[str]:
        """收集用于 Jev 话题判断的候选问题（专属表 + 全局表，去重保序）。"""
        seen: set = set()
        out: List[str] = []
        for table in self._candidate_tables(scope, scope_id):
            for entry in table.entries:
                if not entry.get("enabled", True):
                    continue
                q = str(entry.get("question") or "").strip()
                if q and q not in seen:
                    seen.add(q)
                    out.append(q)
        return out

    def all_tables(self) -> List[QATable]:
        order = {SCOPE_GLOBAL: 0, SCOPE_GROUP: 1, SCOPE_PRIVATE: 2}
        return sorted(
            self.tables.values(),
            key=lambda t: (order.get(t.scope, 9), t.scope_id),
        )

    def scope_summary(self) -> Dict[str, Any]:
        groups: List[str] = []
        privates: List[str] = []
        for t in self.tables.values():
            if t.scope == SCOPE_GROUP:
                groups.extend(t.ids)
            elif t.scope == SCOPE_PRIVATE:
                privates.extend(t.ids)
        global_table = self.tables.get(SCOPE_GLOBAL)
        return {
            "global_entries": len(global_table.entries) if global_table else 0,
            "groups": sorted(set(groups)),
            "privates": sorted(set(privates)),
            "total_entries": sum(len(t.entries) for t in self.tables.values()),
            "table_count": len(self.tables),
        }

    def table_list(self) -> List[Dict[str, Any]]:
        """供页面使用的表清单：每张表带上 ids 与 name。"""
        return [
            {
                "key": t.key,
                "scope": t.scope,
                "scope_id": t.scope_id,
                "ids": list(t.ids),
                "name": t.name,
                "label": t.label,
                "entries": t.entries,
                "is_global": t.scope == SCOPE_GLOBAL,
            }
            for t in self.all_tables()
        ]

    # ── 变更 ──────────────────────────────────────────────
    def replace_table(
        self,
        scope: str,
        scope_id: str,
        entries: Iterable[Any],
        answers: Optional[Dict[str, Any]] = None,
        ids: Optional[Iterable[str]] = None,
        name: Optional[str] = None,
        key: Optional[str] = None,
    ) -> QATable:
        """整体替换某张表的内容（含答案池、服务 ID 列表与名称）。

        key 给定时按存储主键精确指向目标表，避免改 ID 列表时找不到原表。
        """
        table = None
        if key:
            table = self.tables.get(str(key))
        if table is None:
            table = self.get_table(scope, scope_id)

        if ids is not None:
            table.ids = []
            for raw in ids:
                text = str(raw).strip()
                if text and text not in table.ids:
                    table.ids.append(text)
            table.scope_id = table.ids[0] if table.ids else ""
        if name is not None:
            table.name = str(name).strip()

        table.entries = []
        for item in entries or []:
            norm = normalize_entry(item)
            if norm:
                table.entries.append(norm)
        if answers is not None:
            table.answers = {}
            if isinstance(answers, dict):
                for key, raw in answers.items():
                    k = str(key).strip()
                    if k:
                        table.answers[k] = normalize_answer(raw)
        # 清理已不存在的 answer_key 引用，避免悬空
        valid = set(table.answers.keys())
        for entry in table.entries:
            k = str(entry.get("answer_key") or "").strip()
            if k and k not in valid:
                entry.pop("answer_key", None)

        # ID 列表变更后重新挂载存储键（旧键若与当前表不一致则迁移）
        old_key = str(key) if key else None
        new_key = table.key
        if old_key and old_key != new_key:
            self.tables.pop(old_key, None)
        self.tables[new_key] = table
        return table

    def delete_table(self, scope: str, scope_id: str = "", key: Optional[str] = None) -> bool:
        if key and str(key) in self.tables:
            return self.tables.pop(str(key), None) is not None
        if scope == SCOPE_GLOBAL:
            scope_id = ""
        target = self.find_table_by_id(scope, scope_id) if scope_id else self.tables.get(scope)
        if target is None:
            return False
        return self.tables.pop(target.key, None) is not None

    def delete_table_by_key(self, key: str) -> bool:
        return self.tables.pop(str(key or ""), None) is not None

    # ── KeyReply 导入 ─────────────────────────────────────
    @staticmethod
    def parse_keyreply_triggers(data: Any) -> List[Dict[str, Any]]:
        """解析 KeyReply 的 triggers 结构。

        KeyReply 的 triggers 形如：
            {"{'text': '问题', 'images': []}": {'text': '答案', 'images': []}}
        键是 Python 字面量字符串，这里用 ast.literal_eval 安全解析（不使用 eval）。
        """
        import ast

        triggers = data
        if isinstance(data, dict) and "triggers" in data:
            triggers = data.get("triggers")
        if not isinstance(triggers, dict):
            return []

        out: List[Dict[str, Any]] = []
        for key, answer in triggers.items():
            question_text = ""
            question_images: List[str] = []
            if isinstance(key, dict):
                question_text = str(key.get("text") or "")
                question_images = [str(x) for x in (key.get("images") or [])]
            else:
                try:
                    parsed = ast.literal_eval(str(key))
                    if isinstance(parsed, dict):
                        question_text = str(parsed.get("text") or "")
                        question_images = [str(x) for x in (parsed.get("images") or [])]
                    else:
                        question_text = str(parsed)
                except (ValueError, SyntaxError):
                    # 不是 Python 字面量就按纯文本处理
                    question_text = str(key)

            question_text = question_text.strip()
            if not question_text:
                continue
            answer_norm = normalize_answer(answer)
            out.append({
                "question": question_text,
                "answer": answer_norm,
                "enabled": True,
            })
        return out

    def import_keyreply_file(self, path: Path, scope: str = SCOPE_GLOBAL, scope_id: str = "",
                             replace: bool = False) -> Dict[str, Any]:
        """从 KeyReply 的 triggers.yml 导入到指定作用域。

        replace=True 时用来源文件整体替换目标表（真实「复制」语义）；
        否则按问题去重后追加。
        """
        import yaml

        path = Path(path)
        if not path.exists():
            return {"ok": False, "message": f"文件不存在: {path}", "imported": 0}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except Exception as e:
            return {"ok": False, "message": f"解析失败: {e}", "imported": 0}

        entries = self.parse_keyreply_triggers(data)
        if not entries:
            return {"ok": False, "message": "未在该文件中找到任何问答对", "imported": 0}

        table = self.get_table(scope, scope_id)
        if replace:
            table.entries = list(entries)
            table.answers = {}
            added = len(entries)
            skipped = 0
        else:
            existing = {e.get("question") for e in table.entries}
            added = 0
            for entry in entries:
                if entry["question"] in existing:
                    continue
                table.entries.append(entry)
                existing.add(entry["question"])
                added += 1
            skipped = len(entries) - added
        # 把答案相同的 Q 归入同一个答案池条目（多 Q → 一 A）
        regroup_answers(table)
        self.save()
        action = "复制" if replace else "导入"
        return {
            "ok": True,
            "message": f"{action}完成：{added} 条问答对"
            + ("" if replace else f"（跳过 {skipped} 条重复）"),
            "imported": added,
            "skipped": skipped,
            "total": len(entries),
            "replace": bool(replace),
        }

    def keyreply_candidates(self, astrbot_root: Optional[Path] = None) -> List[Path]:
        """列出所有会被探测的 KeyReply 数据文件路径（含不存在的）。"""
        candidates: List[Path] = []
        bases: List[Path] = []
        if astrbot_root:
            bases.append(Path(astrbot_root))
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            bases.append(Path(get_astrbot_data_path()))
        except Exception:
            pass
        bases.append(Path.cwd() / "data")

        for base in bases:
            candidates.append(base / "plugins" / "keyword_reply" / "triggers.yml")
            candidates.append(base / "plugin_data" / "keyword_reply" / "triggers.yml")
            candidates.append(base / "plugins" / "astrbot_plugin_KeyReply" / "triggers.yml")
            candidates.append(base / "plugins" / "astrbot_plugin_keyreply" / "triggers.yml")

        seen: set = set()
        unique: List[Path] = []
        for c in candidates:
            try:
                resolved = str(c.resolve())
            except Exception:
                resolved = str(c)
            if resolved in seen:
                continue
            seen.add(resolved)
            unique.append(c)
        return unique

    def find_keyreply_files(self, astrbot_root: Optional[Path] = None) -> List[str]:
        """探测已存在的 KeyReply 数据文件，供页面显示候选。"""
        found: List[str] = []
        for c in self.keyreply_candidates(astrbot_root):
            if c.exists():
                try:
                    found.append(str(c.resolve()))
                except Exception:
                    found.append(str(c))
        return found

    def copy_keyreply_into_store(self, path: Optional[Path] = None, scope: str = SCOPE_GLOBAL,
                                 scope_id: str = "", replace: bool = False) -> Dict[str, Any]:
        """把 KeyReply 的问答表复制到本插件自己的数据目录。

        与 import_keyreply_file 的区别：本方法面向「一键按钮」场景，
        会自动探测来源文件，并返回可读的来源/目标路径与统计信息。
        """
        source: Optional[Path] = None
        if path:
            source = Path(path)
            if not source.exists():
                return {
                    "ok": False,
                    "message": f"指定的文件不存在：{source}",
                    "source_path": str(source),
                    "target_path": str(self.path),
                    "searched": [str(p) for p in self.keyreply_candidates()],
                }
        else:
            found = self.find_keyreply_files()
            if not found:
                return {
                    "ok": False,
                    "message": "未找到 KeyReply 的数据文件，请确认 KeyReply 插件已使用过「开始记录」功能",
                    "source_path": "",
                    "target_path": str(self.path),
                    "searched": [str(p) for p in self.keyreply_candidates()],
                }
            source = Path(found[0])

        result = self.import_keyreply_file(source, scope, scope_id, replace=replace)
        result["source_path"] = str(source)
        result["target_path"] = str(self.path)
        return result
