"""晋升候选文件的路径、渲染与持久化实现。"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from trowel_py.memory.promotion_policy import PromotionPolicy
from trowel_py.memory.recompute import NoteEffect
from trowel_py.memory.types import Note


def candidates_dir(root: Path, directory: str) -> Path:
    return root / directory


def hash_ids(ids: Iterable[str], hash_factory: Any) -> str:
    joined = "|".join(sorted(ids))
    return hash_factory(joined.encode("utf-8")).hexdigest()[:16]


def safe_candidate_path(
    root: Path,
    memory_id: str,
    *,
    safe_pattern: Any,
    candidates_dir_fn: Callable[[Path], Path],
) -> Path | None:
    if not memory_id or not safe_pattern.match(memory_id):
        return None
    return candidates_dir_fn(root) / f"{memory_id}.md"


def policy_hash(policy: PromotionPolicy, *, hash_factory: Any, json_module: Any) -> str:
    return hash_factory(
        json_module.dumps(policy.to_dict(), sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def candidate_body(note: Note, eff: NoteEffect, policy: PromotionPolicy) -> str:
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


def write_candidate(
    root: Path,
    note: Note,
    eff: NoteEffect,
    policy: PromotionPolicy,
    today: str,
    *,
    safe_candidate_path_fn: Callable[[Path, str], Path | None],
    policy_hash_fn: Callable[[PromotionPolicy], str],
    hash_ids_fn: Callable[[Iterable[str]], str],
    candidate_body_fn: Callable[[Note, NoteEffect, PromotionPolicy], str],
    dump_frontmatter_fn: Callable[[dict[str, Any], str], str],
    logger: Any,
) -> Path | None:
    path = safe_candidate_path_fn(root, note.memory_id)
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
        "policy_hash": policy_hash_fn(policy),
        "helpful_sessions": eff.helpful_refs,
        "harmful_sessions": eff.harmful_refs,
        "distinct_days": eff.distinct_days,
        "helpful_session_ids_hash": hash_ids_fn(eff.helpful_sessions),
        "harmful_session_ids_hash": hash_ids_fn(eff.harmful_sessions),
        "generated_at": today,
        "status": "candidate",
    }
    path.write_text(
        dump_frontmatter_fn(fm, candidate_body_fn(note, eff, policy)), encoding="utf-8"
    )
    return path


def mark_blocked(
    path: Path,
    policy: PromotionPolicy,
    today: str,
    reason: str,
    *,
    split_frontmatter_fn: Callable[[str], tuple[dict[str, Any] | None, str]],
    dump_frontmatter_fn: Callable[[dict[str, Any], str], str],
    logger: Any,
) -> None:
    fm, body = split_frontmatter_fn(path.read_text(encoding="utf-8"))
    if fm is None:
        logger.warning("candidate %s has no frontmatter; not marking blocked", path)
        return
    new_fm: dict[str, Any] = dict(fm)
    new_fm["status"] = "blocked"
    new_fm["blocked_reason"] = reason
    new_fm["policy_version"] = policy.version
    new_fm["blocked_at"] = today
    path.write_text(dump_frontmatter_fn(new_fm, body), encoding="utf-8")
