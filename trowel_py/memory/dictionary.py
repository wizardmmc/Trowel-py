"""dictionary L0/L1 auto-regeneration (slice-040-c C-3).

From ``notes/`` cluster into domains (LLM-assisted, S1 format) → L0 root index
(每领域: 讲啥 + 双向触发词) + L1 per-domain files (每条: 标题 + summary + 触发词).

First-time full build + daily incremental sync (new notes → existing domain L1
append). Never hand-maintained (S1: manual indexes rot). Atomic replace on
apply; failure leaves the old index. The search path never triggers a rebuild
(C-3) — only the CLI and review_job do.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from trowel_py.llm.client import LLMProvider
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import Note

logger = logging.getLogger(__name__)

_DICT_L0 = "dictionary-L0.md"
_DICT_L1_DIR = "dictionary-L1"

_CLUSTER_SYS = (
    "你是记忆字典生成器。把笔记按主题分组为领域。每领域给: name(kebab-case英文,如 telecom-fraud)、"
    "description(一句话讲啥)、triggers(双向触发词:领域内容词+常见查询词,逗号分隔)、"
    "note_ids(该领域笔记id列表,只从给定的id里选)。只输出 JSON,不要解释。"
)
_CLUSTER_USER_TMPL = (
    "笔记列表(序号. 标题 — 摘要):\n{notes}\n\n"
    '输出 JSON: {{"domains": [{{"name":"...","description":"...","triggers":"...","note_ids":["1","2"]}}]}}\n'
    "note_ids 填上面笔记的序号(字符串)。"
)

_ASSIGN_SYS = (
    "你是记忆字典分类器。把新笔记归入最相关的现有领域。只从给定领域列表里选,给出 note_id→domain 映射。"
    "如果都不相关,归 misc。只输出 JSON。"
)


def _cluster_notes(
    notes_with_id: list[tuple[str, Note]], provider: LLMProvider
) -> list[dict[str, Any]]:
    """LLM-cluster notes into domains. Returns list of domain dicts."""
    if not notes_with_id:
        return []
    lines = [
        f"{i}. {n.title} — {n.summary}"
        for i, (_stem, n) in enumerate(notes_with_id, 1)
    ]
    user = _CLUSTER_USER_TMPL.format(notes="\n".join(lines))
    raw = provider.complete(_CLUSTER_SYS, user)
    return _parse_cluster(raw, notes_with_id)


def _parse_cluster(
    raw: str, notes_with_id: list[tuple[str, Note]]
) -> list[dict[str, Any]]:
    """Parse LLM JSON; validate note_ids against real stems; orphans → misc."""
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        logger.warning("dictionary: no JSON in cluster response")
        return _fallback_each_own(notes_with_id)
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        logger.warning("dictionary: invalid cluster JSON")
        return _fallback_each_own(notes_with_id)
    valid_stems = {s for s, _ in notes_with_id}
    domains: list[dict[str, Any]] = []
    for d in data.get("domains", []):
        name = _slugify_domain(str(d.get("name", "")).strip())
        if not name:
            continue
        note_ids: list[str] = []
        for x in d.get("note_ids", []):
            xs = str(x)
            if xs in valid_stems:  # LLM returned a stem directly
                note_ids.append(xs)
                continue
            try:  # LLM returned a 1-based index into the note list
                idx = int(xs) - 1
                if 0 <= idx < len(notes_with_id):
                    note_ids.append(notes_with_id[idx][0])
            except (ValueError, TypeError):
                pass
        if not note_ids:
            continue
        domains.append({
            "name": name,
            "description": str(d.get("description", "")).strip(),
            "triggers": str(d.get("triggers", "")).strip(),
            "note_ids": note_ids,
        })
    assigned = {nid for d in domains for nid in d["note_ids"]}
    orphans = [s for s, _ in notes_with_id if s not in assigned]
    if orphans:
        domains.append({
            "name": "misc", "description": "未归类笔记（待全量重建时重新聚类）",
            "triggers": "", "note_ids": orphans,
        })
    return domains


def _fallback_each_own(notes_with_id: list[tuple[str, Note]]) -> list[dict[str, Any]]:
    """If LLM fails, put each note in its own misc domain (never block on LLM)."""
    return [{
        "name": "misc", "description": "LLM 聚类失败，全部归 misc 待重试",
        "triggers": "", "note_ids": [s for s, _ in notes_with_id],
    }]


def _slugify_domain(name: str) -> str:
    s = re.sub(r"[\s/]+", "-", name.strip())
    s = re.sub(r"[^a-zA-Z0-9一-鿿-]", "", s)
    return s.lower() or "misc"


def _render_l0(domains: list[dict[str, Any]]) -> str:
    lines = [
        "这是 memory 笔记的根索引。先用下面的领域列表定位该去哪个 L1，"
        "再 read 对应 L1 文件找具体笔记。",
        "",
    ]
    for d in domains:
        lines.append(
            f"### {d['name']}（{len(d['note_ids'])} 条 → read dictionary-L1/{d['name']}.md）"
        )
        if d["description"]:
            lines.append(d["description"])
        if d["triggers"]:
            lines.append(f"触发词：{d['triggers']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_l1(domain: dict[str, Any], notes_by_id: dict[str, Note]) -> str:
    lines = [f"# {domain['name']}", ""]
    if domain["description"]:
        lines.append(f"{domain['description']}。")
        lines.append("")
    for stem in domain["note_ids"]:
        n = notes_by_id.get(stem)
        if n is None:
            continue
        triggers = ", ".join(n.tags) if n.tags else n.title
        lines.append(
            f"- **{n.title}** → `notes/{stem}.md`：{n.summary}｜触发词：{triggers}"
        )
    return "\n".join(lines) + "\n"


def derive_dictionary_full(
    root: Path | str, provider: LLMProvider
) -> dict[str, Any]:
    """Full cluster all non-retired notes → {L0, L1: {domain: text}, domains}. Does NOT write."""
    store = MemoryStore(root)
    notes_with_id = store.load_notes_with_id({"retired": False})
    domains = _cluster_notes(notes_with_id, provider)
    notes_by_id = {s: n for s, n in notes_with_id}
    l1 = {d["name"]: _render_l1(d, notes_by_id) for d in domains}
    return {"L0": _render_l0(domains), "L1": l1, "domains": domains}


def _atomic_write(root: Path, l0_text: str, l1_files: dict[str, str]) -> None:
    """Write L0 + L1 (overwrite the L1 files we produce; stale L1 files left)."""
    l0_path = root / _DICT_L0
    l1_dir = root / _DICT_L1_DIR
    l1_dir.mkdir(parents=True, exist_ok=True)
    l0_path.write_text(l0_text, encoding="utf-8")
    for name, text in l1_files.items():
        (l1_dir / f"{name}.md").write_text(text, encoding="utf-8")


def rebuild_dictionary(
    root: Path | str, *, apply: bool, provider: LLMProvider
) -> dict[str, Any]:
    """Full rebuild. apply=True writes; dry-run returns a preview."""
    result = derive_dictionary_full(root, provider)
    if not apply:
        return {
            "apply": False,
            "L0": result["L0"],
            "L1_keys": list(result["L1"].keys()),
            "domain_count": len(result["domains"]),
        }
    _atomic_write(Path(root), result["L0"], result["L1"])
    return {
        "apply": True,
        "domain_count": len(result["domains"]),
        "L1_keys": list(result["L1"].keys()),
    }


def _parse_l0_domains(l0_text: str) -> list[str]:
    """Extract domain names from L0 (### <name>（... → read dictionary-L1/<name>.md）)."""
    return re.findall(r"### (\S+?)（\d+ 条", l0_text)


def sync_dictionary_incremental(
    root: Path | str, new_note_ids: list[str], provider: LLMProvider
) -> dict[str, Any]:
    """Append new notes to existing domain L1 files (incremental, no re-cluster).

    If no dictionary exists, falls back to a full rebuild. Notes that fit no
    existing domain go to ``misc``.
    """
    root_path = Path(root)
    l0_path = root_path / _DICT_L0
    l1_dir = root_path / _DICT_L1_DIR
    if not l0_path.exists() or not l1_dir.exists():
        return rebuild_dictionary(root, apply=True, provider=provider)
    if not new_note_ids:
        return {"synced": 0, "domain_count": len(_parse_l0_domains(l0_path.read_text(encoding="utf-8")))}
    store = MemoryStore(root)
    notes_by_id = {s: n for s, n in store.load_notes_with_id()}
    new_notes = [(s, notes_by_id[s]) for s in new_note_ids if s in notes_by_id]
    if not new_notes:
        return {"synced": 0}
    domains = _parse_l0_domains(l0_path.read_text(encoding="utf-8"))
    assignment = _assign_domains(new_notes, domains, provider)
    # group new notes by assigned domain
    by_domain: dict[str, list[str]] = {d: [] for d in domains}
    by_domain.setdefault("misc", [])
    for stem, dom in assignment.items():
        by_domain.setdefault(dom, []).append(stem)
    for dom, stems in by_domain.items():
        if not stems:
            continue
        l1_file = l1_dir / f"{dom}.md"
        existing = l1_file.read_text(encoding="utf-8") if l1_file.exists() else f"# {dom}\n\n"
        for stem in stems:
            n = notes_by_id[stem]
            triggers = ", ".join(n.tags) if n.tags else n.title
            existing += f"- **{n.title}** → `notes/{stem}.md`：{n.summary}｜触发词：{triggers}\n"
        l1_file.write_text(existing, encoding="utf-8")
    # slice-040-c: if sync used a domain not in L0 (e.g. misc fallback), append
    # it to L0 so search can discover the L1 file (codex P2-2).
    used_not_in_l0 = [d for d, s in by_domain.items() if s and d not in domains]
    if used_not_in_l0:
        l0_text = l0_path.read_text(encoding="utf-8")
        for d in used_not_in_l0:
            l0_text += (
                f"\n### {d}（{len(by_domain[d])} 条 → read dictionary-L1/{d}.md）\n"
                f"未归类笔记（待全量重建时重新聚类）\n\n"
            )
        l0_path.write_text(l0_text, encoding="utf-8")
    return {"synced": len(new_notes), "assigned": assignment}


def _assign_domains(
    new_notes: list[tuple[str, Note]], domains: list[str], provider: LLMProvider
) -> dict[str, str]:
    """LLM-assign each new note to an existing domain (misc if none fit)."""
    lines = [f"- [{s}] {n.title} — {n.summary}" for s, n in new_notes]
    user = f"现有领域: {', '.join(domains)}\n新笔记:\n" + "\n".join(lines) + (
        f'\n输出 JSON: {{"assign": [{{"note_id":"...","domain":"..."}}]}}'
    )
    raw = provider.complete(_ASSIGN_SYS, user)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {s: "misc" for s, _ in new_notes}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {s: "misc" for s, _ in new_notes}
    out: dict[str, str] = {}
    for item in data.get("assign", []):
        nid = str(item.get("note_id", ""))
        dom = str(item.get("domain", "misc"))
        if nid and nid in {s for s, _ in new_notes}:
            out[nid] = dom if dom in domains or dom == "misc" else "misc"
    for s, _ in new_notes:
        out.setdefault(s, "misc")
    return out
