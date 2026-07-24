"""根据会话级证据评估笔记是否应生成 layer-one 候选。

评估只写入或阻断按 ``memory_id`` 覆盖的候选文件，不直接修改 ``core.md``；
候选仍需人工批准。
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

from trowel_py.memory.promotion import candidates as _candidate_io
from trowel_py.memory.promotion_policy import PromotionPolicy, default_policy
from trowel_py.memory.recompute import NoteEffect, compute_note_effects
from trowel_py.memory.store import MemoryStore, _dump_frontmatter, _split_frontmatter
from trowel_py.memory.types import Note

_CANDIDATES_DIR = "meta/core-candidates"
_SAFE_MEMORY_ID = re.compile(r"^[A-Za-z0-9_-]+$")

logger = logging.getLogger(__name__)


def _today_str(today: str | None) -> str:
    return today or date.today().isoformat()


def _candidates_dir(root: Path) -> Path:
    return _candidate_io.candidates_dir(root, _CANDIDATES_DIR)


def _hash_ids(ids: Iterable[str]) -> str:
    """返回与顺序无关的会话集合摘要，用于区分计数相同的证据。"""
    return _candidate_io.hash_ids(ids, hashlib.sha256)


def _safe_candidate_path(root: Path, memory_id: str) -> Path | None:
    """仅接受不能逃逸候选目录的 Memory 标识。"""
    return _candidate_io.safe_candidate_path(
        root,
        memory_id,
        safe_pattern=_SAFE_MEMORY_ID,
        candidates_dir_fn=_candidates_dir,
    )


def _policy_hash(policy: PromotionPolicy) -> str:
    """摘要完整策略，使同版本的不同阈值仍可追溯。"""
    return _candidate_io.policy_hash(
        policy, hash_factory=hashlib.sha256, json_module=json
    )


def _gap_flags(
    note: Note, eff: NoteEffect | None, policy: PromotionPolicy
) -> list[str]:
    """返回笔记尚未满足的策略阈值。"""
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
    return _candidate_io.candidate_body(note, eff, policy)


def _write_candidate(
    root: Path, note: Note, eff: NoteEffect, policy: PromotionPolicy, today: str
) -> Path | None:
    return _candidate_io.write_candidate(
        root,
        note,
        eff,
        policy,
        today,
        safe_candidate_path_fn=_safe_candidate_path,
        policy_hash_fn=_policy_hash,
        hash_ids_fn=_hash_ids,
        candidate_body_fn=_candidate_body,
        dump_frontmatter_fn=_dump_frontmatter,
        logger=logger,
    )


def _mark_blocked(path: Path, policy: PromotionPolicy, today: str, reason: str) -> None:
    _candidate_io.mark_blocked(
        path,
        policy,
        today,
        reason,
        split_frontmatter_fn=_split_frontmatter,
        dump_frontmatter_fn=_dump_frontmatter,
        logger=logger,
    )


def evaluate_promotion(
    root: Path | str,
    policy: PromotionPolicy | None = None,
    *,
    local_tz: Any | None = None,
    today: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """评估所有 active 笔记；dry-run 时只返回差距报告，不写候选文件。"""
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
            # 即使阈值为零，无存续证据的笔记也不能晋升。
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
                        _write_candidate(root_path, note, eff, active_policy, today_str)
                    candidates.append(note.memory_id)
            elif (
                harmful > active_policy.max_harmful_sessions
                and cand_path is not None
                and cand_path.exists()
            ):
                # 新增有害反例后必须阻断既有候选，不能继续等待人工批准。
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
