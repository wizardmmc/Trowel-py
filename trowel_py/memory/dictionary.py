"""dictionary L0/L1 regeneration as a derived, verifiable view of notes.

From ``notes/`` (the only source of truth) cluster into domains (LLM-assisted)
→ L0 root index + L1 per-domain files. slice-064: the index is no longer
hand-maintained or incrementally appended — it is fully rebuilt from the active
notes and provably agrees with them (see ``dictionary_check`` for the
consistency contract + ``dictionary_state`` for the consistent/stale/missing
state). Atomic staging-then-replace on publish; failure leaves the old index
and marks the state stale. The search path never triggers a rebuild (C-8) —
only ``rebuild_dictionary`` / ``ensure_dictionary_consistent`` do, and only the
short publish holds the exclusive dictionary lock (N2: the LLM derive runs
outside it).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trowel_py.llm.client import LLMProvider
from trowel_py.memory.dictionary_check import (
    _check_dictionary_locked,
    _evaluate,
    compute_rendered_hash,
    compute_source_hash,
    derive_active_corpus,
)
from trowel_py.memory.dictionary_lock import dictionary_lock
from trowel_py.memory.dictionary_state import (
    DictionaryState,
    load_state,
    save_state,
)
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


def _l1_entry(stem: str, note: Note) -> str:
    """One L1 line. The ``<!-- @stem ... -->`` anchor is content-independent
    so the consistency check can round-trip the stem even when the stem itself
    contains a backtick (which would break the `` `notes/{stem}.md` `` code
    span). Every rendered L1 entry goes through here so the on-disk format has
    one definition."""
    triggers = ", ".join(note.tags) if note.tags else note.title
    return (
        f"- **{note.title}** → `notes/{stem}.md`：{note.summary}"
        f"｜触发词：{triggers} <!-- @stem {stem} -->"
    )


def _render_l1(domain: dict[str, Any], notes_by_id: dict[str, Note]) -> str:
    lines = [f"# {domain['name']}", ""]
    if domain["description"]:
        lines.append(f"{domain['description']}。")
        lines.append("")
    for stem in domain["note_ids"]:
        n = notes_by_id.get(stem)
        if n is None:
            continue
        lines.append(_l1_entry(stem, n))
    return "\n".join(lines) + "\n"


def derive_dictionary_full(
    root: Path | str, provider: LLMProvider
) -> dict[str, Any]:
    """Full cluster all ACTIVE notes → {L0, L1: {domain: text}, domains}. Does NOT write.

    slice-064 C-3: superseded / contradicted / retired are note facts but never
    enter the default index, so the corpus clustered here is active-only (was
    ``{"retired": False}``, which still surfaced superseded/contradicted notes).
    """
    store = MemoryStore(root)
    notes_with_id = store.load_notes_with_id({"status": "active"})
    domains = _cluster_notes(notes_with_id, provider)
    notes_by_id = {s: n for s, n in notes_with_id}
    l1 = {d["name"]: _render_l1(d, notes_by_id) for d in domains}
    return {"L0": _render_l0(domains), "L1": l1, "domains": domains}


_STALE_DIR_TTL = 3600  # seconds; staging/trash dirs older than this are reaped


def _gc_stale_dict_dirs(root: Path) -> None:
    """Reap staging/trash dirs from crashed previous runs (slice-064 L-3).

    A kill -9 mid-swap can leave ``.dict-l1-staging-*`` / ``.dict-l1-trash-*``
    under ``meta/``; without GC they accumulate. Only dirs older than
    ``_STALE_DIR_TTL`` are removed, so a concurrent rebuild's fresh dirs
    (different uuid suffix, just created) are never touched.
    """
    meta = root / "meta"
    if not meta.exists():
        return
    now = time.time()
    for p in meta.iterdir():
        name = p.name
        if not (name.startswith(".dict-l1-staging-") or name.startswith(".dict-l1-trash-")):
            continue
        try:
            if now - p.stat().st_mtime > _STALE_DIR_TTL:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            continue


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _mark_stale(root: Path, reason: str) -> None:
    """Stamp the state ``stale`` (slice-064 §4: failure visible + retryable).

    The last-good ``source_hash`` is preserved (C-7) so the operator still sees
    what the index last agreed with. Best-effort: a state-write failure here
    only means the next check re-derives from the index.
    """
    try:
        prev = load_state(root)
        save_state(root, prev.with_failure(reason, _now_iso()))
    except Exception:  # noqa: BLE001 — state is best-effort observability
        logger.warning("could not stamp dictionary stale: %s", reason, exc_info=True)


def _atomic_replace(
    root: Path, l0_text: str, l1_files: dict[str, str]
) -> None:
    """Swap the live L0 + whole L1 dir for a freshly rendered set (C-4/C-5).

    Writes the new L1 into a staging dir under ``meta/`` (same filesystem, so
    rename is atomic), then swaps: old L1 → trash, staging → live, new L0 via
    temp + ``os.replace``. On ANY failure the old L0 and every old L1 file are
    restored byte-for-byte (rename back), so the index is never left half-new
    (C-4: L0/L1 same generation). Orphan L1 files from a prior domain set are
    dropped because the whole dir is replaced (C-5).
    """
    meta = root / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    _gc_stale_dict_dirs(root)  # reap staging/trash from any crashed prior run
    suffix = uuid.uuid4().hex[:8]
    staging_l1 = meta / f".dict-l1-staging-{suffix}"
    trash = meta / f".dict-l1-trash-{suffix}"
    l0_path = root / _DICT_L0
    tmp_l0 = l0_path.with_name(l0_path.name + f".tmp-{suffix}")
    swapped = False
    try:
        staging_l1.mkdir(parents=True, exist_ok=True)
        for name, text in l1_files.items():
            (staging_l1 / f"{name}.md").write_text(text, encoding="utf-8")
        l1_dir = root / _DICT_L1_DIR
        if l1_dir.exists():
            l1_dir.rename(trash)
        staging_l1.rename(l1_dir)
        swapped = True
        tmp_l0.write_text(l0_text, encoding="utf-8")
        os.replace(tmp_l0, l0_path)
    except Exception:
        # Rollback to the pre-call index, byte-for-byte. ``ignore_errors`` on
        # the rmtree is deliberate — a failed cleanup must not mask the original
        # exception; any leftover is bounded + self-healing (the next check
        # flags stale, _gc_stale_dict_dirs reaps old staging/trash). (F8)
        if swapped:
            # F8: free the live path ATOMICALLY (rename aside) before restoring
            # the old generation, so a partial rmtree can't leave the path
            # occupied and block the restore rename.
            live = root / _DICT_L1_DIR
            if live.exists():
                aside = meta / f".dict-l1-failed-{suffix}"
                try:
                    live.rename(aside)
                    shutil.rmtree(aside, ignore_errors=True)
                except OSError:
                    shutil.rmtree(live, ignore_errors=True)
            if trash.exists() and not live.exists():
                trash.rename(live)
        elif trash.exists() and not (root / _DICT_L1_DIR).exists():
            trash.rename(root / _DICT_L1_DIR)
        raise
    finally:
        # reap every artifact we created (staging dir + L0 tmp). trash is kept
        # until the success path drops it. (F10)
        shutil.rmtree(staging_l1, ignore_errors=True)
        if tmp_l0.exists():
            tmp_l0.unlink(missing_ok=True)
    if trash.exists():
        shutil.rmtree(trash, ignore_errors=True)


def _derive_and_stage(
    root: Path | str, provider: LLMProvider, apply: bool
) -> dict[str, Any]:
    """Derive (LLM cluster) + staging check. Holds NO lock, writes NO state,
    marks nothing stale -- the caller owns those (slice-064 N2: the LLM cluster
    runs outside the lock so a slow provider does not block search/check).

    Returns a staged dict (``l0_text``/``l1_files``/``source_hash``/
    ``rendered_hash``/``check``/``domain_count``) on success, or
    ``{apply, error, reason, ...}`` on derive / staging-check failure.
    """
    root_path = Path(root)
    try:
        result = derive_dictionary_full(root_path, provider)
    except Exception as exc:  # noqa: BLE001 -- provider/network, surface + retry
        return {"apply": apply, "error": "derive_failed",
                "reason": f"derive failed: {exc}"}
    l0_text = result["L0"]
    l1_files = result["L1"]
    corpus = derive_active_corpus(root_path)
    staging_report = _evaluate(
        corpus, l0_text, l1_files, state_hash=None, baseline_required=False,
    )
    if staging_report["status"] != "consistent":
        # The rendered set disagrees with the note facts (a render/cluster
        # bug). Never publish a known-bad index.
        return {"apply": apply, "error": "staging_inconsistent",
                "reason": "staging check rejected the rendered index",
                "check": staging_report}
    return {
        "l0_text": l0_text,
        "l1_files": l1_files,
        "source_hash": compute_source_hash(corpus),
        "rendered_hash": compute_rendered_hash(l0_text, l1_files),
        "domain_count": len(result["domains"]),
        "check": staging_report,
    }


def rebuild_dictionary(
    root: Path | str, *, apply: bool, provider: LLMProvider
) -> dict[str, Any]:
    """Atomic full rebuild (slice-064 S3, N2: derive outside the lock).

    apply=True: mark stale up-front (crash -> self-heal), derive + stage outside
    the lock, then take LOCK_EX only for the short publish (atomic replace +
    state). ``apply=False`` returns a preview + staging report with no writes.
    Any failure preserves the old index byte-for-byte and marks stale; returns
    an ``error`` dict (never raises) so review/tidy keep going and retry.
    """
    root_path = Path(root)
    if apply:
        _mark_stale(root_path, "rebuild in progress")
    staged = _derive_and_stage(root_path, provider, apply)
    if "error" in staged:
        if apply:
            _mark_stale(root_path, staged.get("reason", staged["error"]))
        logger.warning("dictionary rebuild did not publish: %s", staged["error"])
        return staged
    if not apply:
        return {
            "apply": False,
            "L0": staged["l0_text"],
            "L1_keys": list(staged["l1_files"].keys()),
            "domain_count": staged["domain_count"],
            "check": staged["check"],
        }
    with dictionary_lock(root, exclusive=True):
        try:
            _atomic_replace(root_path, staged["l0_text"], staged["l1_files"])
        except Exception as exc:  # noqa: BLE001 -- IO, surface + retry
            _mark_stale(root_path, f"replace failed: {exc}")
            logger.warning("dictionary replace failed (old index kept): %s", exc)
            return {"apply": True, "error": "replace_failed", "reason": str(exc)}
        # save_state is best-effort (N1): it is an atomic os.replace that
        # essentially never fails; if it did, the new L0/L1 is already correct
        # for the notes while the state stays stale -- the next check sees stale
        # and rebuilds (a redundant rebuild, not data loss).
        try:
            save_state(
                root_path,
                DictionaryState().with_success(
                    staged["source_hash"], staged["rendered_hash"], _now_iso()
                ),
            )
        except Exception as exc:  # noqa: BLE001 -- best-effort state stamp
            logger.warning("dictionary state stamp failed (best-effort): %s", exc)
    return {
        "apply": True,
        "domain_count": staged["domain_count"],
        "L1_keys": list(staged["l1_files"].keys()),
        "source_hash": staged["source_hash"],
        "rendered_hash": staged["rendered_hash"],
        "check": staged["check"],
    }


def ensure_dictionary_consistent(
    root: Path | str, provider: LLMProvider
) -> dict[str, Any]:
    """slice-064 S4: after any batch note change, converge the index.

    check (under LOCK_EX, short) -> if not consistent, mark stale + derive +
    publish (N2: the LLM derive runs OUTSIDE the lock; only the short publish
    holds LOCK_EX, so a slow cluster does not block search/check). Returns
    ``dictionary_status`` (consistent|stale). Never raises -- a rebuild failure
    leaves the state stale (C-7) and is retried on the next run.
    """
    root_path = Path(root)
    with dictionary_lock(root, exclusive=True):
        before = _check_dictionary_locked(root)
        if before["status"] == "consistent":
            return {"dictionary_status": "consistent", "rebuilt": False, "check": before}

    _mark_stale(root_path, "rebuild in progress")
    staged = _derive_and_stage(root_path, provider, apply=True)
    if "error" in staged:
        _mark_stale(root_path, staged.get("reason", staged["error"]))
        logger.warning("dictionary rebuild did not publish: %s", staged["error"])
        return {"dictionary_status": "stale", "rebuild": staged, "check_before": before}
    with dictionary_lock(root, exclusive=True):
        try:
            _atomic_replace(root_path, staged["l0_text"], staged["l1_files"])
            # best-effort state stamp (N1) -- see rebuild_dictionary.
            try:
                save_state(
                    root_path,
                    DictionaryState().with_success(
                        staged["source_hash"], staged["rendered_hash"], _now_iso()
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("dictionary state stamp failed (best-effort): %s", exc)
            after = _check_dictionary_locked(root)
        except Exception as exc:  # noqa: BLE001 -- publish failure
            _mark_stale(root_path, f"publish failed: {exc}")
            logger.warning("dictionary publish failed: %s", exc)
            return {"dictionary_status": "stale",
                    "rebuild": {"error": "publish_failed", "reason": str(exc)},
                    "check_before": before}
    return {
        "dictionary_status": after["status"],
        "rebuilt": True,
        "check_before": before,
        "check_after": after,
        "rebuild": {"apply": True, "source_hash": staged["source_hash"],
                    "rendered_hash": staged["rendered_hash"]},
    }


def mark_dictionary_stale_if_drifted(root: Path | str) -> dict[str, Any]:
    """slice-064 F6: when no provider is available to rebuild, still run the
    read-only check and mark the state stale on drift.

    A daily/tidy run with no LLM provider configured must not silently leave a
    drifted index reported as ``consistent``. This observes the drift (so MCP
    search shows a warning) and stamps ``stale`` so the next run with a
    provider retries the rebuild. Never raises.
    """
    from trowel_py.memory.dictionary_check import check_dictionary

    root_path = Path(root)
    try:
        report = check_dictionary(root_path)
    except Exception as exc:  # noqa: BLE001 — never block the caller
        logger.warning("dictionary check raised: %s", exc)
        return {"dictionary_status": "stale", "error": str(exc)}
    if report["status"] != "consistent":
        _mark_stale(
            root_path, f"drift detected ({report['status']}); no provider to rebuild"
        )
    return {"dictionary_status": report["status"], "rebuilt": False, "check": report}



