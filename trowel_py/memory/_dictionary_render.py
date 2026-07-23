"""从 active notes 派生 dictionary L0/L1 文本，不写入磁盘。"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from trowel_py.llm.client import LLMProvider
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import Note

# 保持拆分前的 logger name，避免既有日志过滤规则失效。
logger = logging.getLogger("trowel_py.memory.dictionary")

_CLUSTER_SYSTEM_PROMPT = (
    "你是记忆字典生成器。把笔记按主题分组为领域。每领域给: name(kebab-case英文,如 telecom-fraud)、"
    "description(一句话讲啥)、triggers(双向触发词:领域内容词+常见查询词,逗号分隔)、"
    "note_ids(该领域笔记id列表,只从给定的id里选)。只输出 JSON,不要解释。"
)
_CLUSTER_USER_TEMPLATE = (
    "笔记列表(序号. 标题 — 摘要):\n{notes}\n\n"
    '输出 JSON: {{"domains": [{{"name":"...","description":"...","triggers":"...","note_ids":["1","2"]}}]}}\n'
    "note_ids 填上面笔记的序号(字符串)。"
)


def derive_dictionary_full(
    root: Path | str,
    provider: LLMProvider,
) -> dict[str, Any]:
    store = MemoryStore(root)
    notes_with_id = store.load_notes_with_id({"status": "active"})
    domains = _cluster_notes(notes_with_id, provider)
    notes_by_id = {stem: note for stem, note in notes_with_id}
    l1_files = {
        domain["name"]: _render_l1(domain, notes_by_id)
        for domain in domains
    }
    return {
        "L0": _render_l0(domains),
        "L1": l1_files,
        "domains": domains,
    }


def _cluster_notes(
    notes_with_id: list[tuple[str, Note]],
    provider: LLMProvider,
) -> list[dict[str, Any]]:
    if not notes_with_id:
        return []
    lines = [
        f"{index}. {note.title} — {note.summary}"
        for index, (_stem, note) in enumerate(notes_with_id, 1)
    ]
    user_prompt = _CLUSTER_USER_TEMPLATE.format(notes="\n".join(lines))
    response = provider.complete(_CLUSTER_SYSTEM_PROMPT, user_prompt)
    return _parse_cluster(response, notes_with_id)


def _parse_cluster(
    response: str,
    notes_with_id: list[tuple[str, Note]],
) -> list[dict[str, Any]]:
    match = re.search(r"\{.*\}", response, re.DOTALL)
    if not match:
        logger.warning("dictionary: no JSON in cluster response")
        return _fallback_to_misc(notes_with_id)
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        logger.warning("dictionary: invalid cluster JSON")
        return _fallback_to_misc(notes_with_id)

    valid_stems = {stem for stem, _note in notes_with_id}
    domains: list[dict[str, Any]] = []
    for candidate in data.get("domains", []):
        name = _slugify_domain(str(candidate.get("name", "")).strip())
        if not name:
            continue
        note_ids = _resolve_note_ids(
            candidate.get("note_ids", []),
            notes_with_id,
            valid_stems,
        )
        if not note_ids:
            continue
        domains.append(
            {
                "name": name,
                "description": str(candidate.get("description", "")).strip(),
                "triggers": str(candidate.get("triggers", "")).strip(),
                "note_ids": note_ids,
            }
        )

    assigned = {
        note_id
        for domain in domains
        for note_id in domain["note_ids"]
    }
    orphans = [
        stem
        for stem, _note in notes_with_id
        if stem not in assigned
    ]
    if orphans:
        domains.append(
            {
                "name": "misc",
                "description": "未归类笔记（待全量重建时重新聚类）",
                "triggers": "",
                "note_ids": orphans,
            }
        )
    return domains


def _resolve_note_ids(
    values: list[Any],
    notes_with_id: list[tuple[str, Note]],
    valid_stems: set[str],
) -> list[str]:
    note_ids: list[str] = []
    for value in values:
        text = str(value)
        if text in valid_stems:
            note_ids.append(text)
            continue
        try:
            index = int(text) - 1
            if 0 <= index < len(notes_with_id):
                note_ids.append(notes_with_id[index][0])
        except (ValueError, TypeError):
            pass
    return note_ids


def _fallback_to_misc(
    notes_with_id: list[tuple[str, Note]],
) -> list[dict[str, Any]]:
    return [
        {
            "name": "misc",
            "description": "LLM 聚类失败，全部归 misc 待重试",
            "triggers": "",
            "note_ids": [stem for stem, _note in notes_with_id],
        }
    ]


def _slugify_domain(name: str) -> str:
    slug = re.sub(r"[\s/]+", "-", name.strip())
    slug = re.sub(r"[^a-zA-Z0-9一-鿿-]", "", slug)
    return slug.lower() or "misc"


def _render_l0(domains: list[dict[str, Any]]) -> str:
    lines = [
        "这是 memory 笔记的根索引。先用下面的领域列表定位该去哪个 L1，"
        "再 read 对应 L1 文件找具体笔记。",
        "",
    ]
    for domain in domains:
        lines.append(
            f"### {domain['name']}（{len(domain['note_ids'])} 条 → "
            f"read dictionary-L1/{domain['name']}.md）"
        )
        if domain["description"]:
            lines.append(domain["description"])
        if domain["triggers"]:
            lines.append(f"触发词：{domain['triggers']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_l1(
    domain: dict[str, Any],
    notes_by_id: dict[str, Note],
) -> str:
    lines = [f"# {domain['name']}", ""]
    if domain["description"]:
        lines.extend([f"{domain['description']}。", ""])
    for stem in domain["note_ids"]:
        note = notes_by_id.get(stem)
        if note is not None:
            lines.append(_render_l1_entry(stem, note))
    return "\n".join(lines) + "\n"


def _render_l1_entry(stem: str, note: Note) -> str:
    # HTML anchor 不受 Markdown code span 转义影响，供一致性检查还原原始 stem。
    triggers = ", ".join(note.tags) if note.tags else note.title
    return (
        f"- **{note.title}** → `notes/{stem}.md`：{note.summary}"
        f"｜触发词：{triggers} <!-- @stem {stem} -->"
    )
