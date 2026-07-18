"""layer-one promotion evaluation from session-level evidence (slice-065 §4/§5).

``evaluate_promotion`` is the policy-driven gate the monthly tidy runs: it
takes the recompute evidence (``compute_note_effects``) and a
``PromotionPolicy``, and for each active note either writes / refreshes a
candidate file, marks a now-contradicted candidate blocked, or records the gap
(the specific thresholds it still misses). It NEVER writes ``core.md`` — a
candidate is for human review (``core approve`` moves it into layer one).

Idempotent: a note's candidate file is named by ``memory_id`` and overwritten,
so re-running never stacks duplicates. A candidate that gained harmful
counter-evidence beyond the policy is flipped to ``status=blocked`` so it stops
masquerading as a valid promotion (C-3).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

from trowel_py.memory.promotion_policy import PromotionPolicy, default_policy
from trowel_py.memory.recompute import NoteEffect, compute_note_effects
from trowel_py.memory.store import MemoryStore, _dump_frontmatter, _split_frontmatter
from trowel_py.memory.types import Note

_CANDIDATES_DIR = "meta/core-candidates"

logger = logging.getLogger(__name__)


def _today_str(today: str | None) -> str:
    return today or date.today().isoformat()


def _candidates_dir(root: Path) -> Path:
    return root / _CANDIDATES_DIR


def _hash_ids(ids: Iterable[str]) -> str:
    """Stable short hash of a session-id set (order-independent) for candidate
    provenance — a reader can tell two candidates apart even when the counts
    match (C-7 replayability)."""
    joined = "|".join(sorted(ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


_SAFE_MEMORY_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _safe_candidate_path(root: Path, memory_id: str) -> Path | None:
    """``meta/core-candidates/<memory_id>.md``, refusing path-escaping ids.

    A ``memory_id`` with ``/``, ``..`` or separators could resolve outside the
    candidates dir (e.g. ``../../core`` → ``root/core.md``) and overwrite a file
    the auto-promote path must never touch (C-8). UUIDv7 ids match; a hostile
    hand-edited id returns None and the caller skips the write.
    """
    if not memory_id or not _SAFE_MEMORY_ID.match(memory_id):
        return None
    return _candidates_dir(root) / f"{memory_id}.md"


def _policy_hash(policy: PromotionPolicy) -> str:
    """Stable hash of the full policy (C-7) — two overrides that share a version
    but differ on thresholds still distinguish their candidates."""
    return hashlib.sha256(
        json.dumps(policy.to_dict(), sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def _gap_flags(note: Note, eff: NoteEffect | None, policy: PromotionPolicy) -> list[str]:
    """The policy thresholds this note still misses (empty ⇒ eligible)."""
    helpful = eff.helpful_refs if eff else 0
    harmful = eff.harmful_refs if eff else 0
    distinct = eff.distinct_days if eff else 0
    gaps: list[str] = []
    if note.kind not in policy.allowed_kinds:
        gaps.append("kind")
    if note.verification not in policy.allowed_verification:
        gaps.append("verification")
    if helpful < policy.min_helpful_sessions:
        gaps.append("helpful_sessions")
    if harmful > policy.max_harmful_sessions:
        gaps.append("harmful_sessions")
    if distinct < policy.min_distinct_days:
        gaps.append("distinct_days")
    return gaps


def _candidate_body(note: Note, eff: NoteEffect, policy: PromotionPolicy) -> str:
    lines = [
        f"# 候选：{note.title}",
        "",
        note.summary or "",
        "",
        "## 晋升依据",
        "",
        f"- 在 {eff.helpful_refs} 个独立用户会话中被判为有帮助"
        f"（策略下限 {policy.min_helpful_sessions}）。",
        f"- 证据跨 {eff.distinct_days} 个不同日期"
        f"（策略下限 {policy.min_distinct_days}）。",
    ]
    if eff.harmful_refs:
        lines.append(
            f"- 存在 {eff.harmful_refs} 个有害反例会话"
            f"（策略上限 {policy.max_harmful_sessions}）。"
        )
    else:
        lines.append("- 没有发现有害反例。")
    lines += [
        f"- 笔记类型 {note.kind}，验证状态 {note.verification}。",
        f"- 依据策略版本 {policy.version}生成；仅供人工 review，"
        "确认后经 approve 移入 core.md。",
        "",
        "## 正文",
        "",
        note.body or "",
    ]
    return "\n".join(lines)


def _write_candidate(
    root: Path, note: Note, eff: NoteEffect, policy: PromotionPolicy, today: str
) -> Path | None:
    path = _safe_candidate_path(root, note.memory_id)
    if path is None:
        logger.warning(
            "unsafe memory_id %r; skipping candidate (C-8 path-escape guard)",
            note.memory_id,
        )
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    fm: dict[str, Any] = {
        "type": "core-candidate",
        "memory_id": note.memory_id,
        "source_title": note.title,
        "kind": note.kind,
        "verification": note.verification,
        "policy_version": policy.version,
        "policy_hash": _policy_hash(policy),
        "helpful_sessions": eff.helpful_refs,
        "harmful_sessions": eff.harmful_refs,
        "distinct_days": eff.distinct_days,
        "helpful_session_ids_hash": _hash_ids(eff.helpful_sessions),
        "harmful_session_ids_hash": _hash_ids(eff.harmful_sessions),
        "generated_at": today,
        "status": "candidate",
    }
    path.write_text(
        _dump_frontmatter(fm, _candidate_body(note, eff, policy)), encoding="utf-8"
    )
    return path


def _mark_blocked(
    path: Path, policy: PromotionPolicy, today: str, reason: str
) -> None:
    """Flip an existing candidate to status=blocked (harmful evidence exceeded).

    A hand-corrupted candidate (no parseable frontmatter) is left untouched
    with a warning rather than rewritten to an empty body.
    """
    fm, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    if fm is None:
        logger.warning("candidate %s has no frontmatter; not marking blocked", path)
        return
    new_fm: dict[str, Any] = dict(fm)
    new_fm["status"] = "blocked"
    new_fm["blocked_reason"] = reason
    new_fm["policy_version"] = policy.version
    new_fm["blocked_at"] = today
    path.write_text(_dump_frontmatter(new_fm, body), encoding="utf-8")


def evaluate_promotion(
    root: Path | str,
    policy: PromotionPolicy | None = None,
    *,
    local_tz: Any | None = None,
    today: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Evaluate every active note against ``policy``; write/refresh/block
    candidate files and return a report (slice-065 §5).

    Args:
        root: the memory root directory.
        policy: the promotion policy (default ``default_policy()``).
        local_tz: timezone for the day boundary (None → system local).
        today: ISO date stamp for the candidate ``generated_at`` field
            (None → wall clock); inject in tests.
        dry_run: when True, compute the gaps/candidates/blocked lists WITHOUT
            writing any candidate file (a read-only "what would change" preview
            — the default for ``trowel memory promotion``).

    Returns:
        ``{policy, candidates, blocked, notes_evaluated, gaps, dry_run}`` where
        ``gaps`` lists every evaluated note with the thresholds it still misses
        (empty ``gaps[i].gaps`` ⇒ that note would be promoted).
    """
    active_policy = policy or default_policy()
    root_path = Path(root)
    today_str = _today_str(today)
    effects = compute_note_effects(root_path, local_tz=local_tz)
    notes = dict(MemoryStore(root_path).load_notes_with_id())

    candidates: list[str] = []
    blocked: list[str] = []
    gaps: list[dict[str, Any]] = []
    evaluated = 0
    for stem, note in notes.items():
        if note.status != "active" or not note.memory_id:
            continue
        evaluated += 1
        eff = effects.get(stem)
        helpful = eff.helpful_refs if eff else 0
        harmful = eff.harmful_refs if eff else 0
        distinct = eff.distinct_days if eff else 0
        cand_path = _safe_candidate_path(root_path, note.memory_id)
        if eff is None:
            # no surviving evidence → never promotes (even at threshold 0);
            # recorded as a gap so the report still accounts for the note.
            miss: list[str] = ["no_evidence"]
            eligible = False
        else:
            miss = _gap_flags(note, eff, active_policy)
            eligible = not miss
            if eligible:
                if cand_path is None:
                    logger.warning(
                        "unsafe memory_id %r; not promoting (C-8)", note.memory_id
                    )
                else:
                    if not dry_run:
                        _write_candidate(
                            root_path, note, eff, active_policy, today_str
                        )
                    candidates.append(note.memory_id)
            elif (
                harmful > active_policy.max_harmful_sessions
                and cand_path is not None
                and cand_path.exists()
            ):
                # a previously-valid candidate gained harmful counter-evidence
                # beyond the policy — stop it masquerading as valid.
                if not dry_run:
                    _mark_blocked(
                        cand_path,
                        active_policy,
                        today_str,
                        reason=(
                            f"harmful_sessions={harmful}"
                            f">{active_policy.max_harmful_sessions}"
                        ),
                    )
                blocked.append(note.memory_id)
        gaps.append(
            {
                "memory_id": note.memory_id,
                "title": note.title,
                "stem": stem,
                "kind": note.kind,
                "verification": note.verification,
                "helpful_sessions": helpful,
                "harmful_sessions": harmful,
                "distinct_days": distinct,
                "gaps": miss,
                "eligible": eligible,
            }
        )
    return {
        "policy": active_policy.to_dict(),
        "candidates": candidates,
        "blocked": blocked,
        "notes_evaluated": evaluated,
        "gaps": gaps,
        "dry_run": dry_run,
    }
