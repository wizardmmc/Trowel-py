"""保存 Tidy 计划快照，执行 Note 变更并支持按快照回滚。"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

from trowel_py.memory.store import MemoryStore

from .models import TidyPlan
from .validation import (
    _REVISE_ALLOWED_FIELDS,
    _memory_id_to_stem,
    validate_plan,
)

_SNAPSHOTS_DIR = "meta/snapshots"


@contextlib.contextmanager
def _tidy_lock(root: Path):
    """尝试独占同一记忆目录的 Tidy 流程。

    不支持 ``fcntl`` 的平台不加锁；支持时采用非阻塞文件锁，锁已被占用会
    抛出 ``BlockingIOError``。调用方负责把需要串行化的完整流程放入上下文。

    Args:
        root: 记忆目录；锁文件写入其 ``meta`` 子目录。

    Yields:
        进入互斥区后将控制权交还调用方；不支持 ``fcntl`` 时直接交还。
    """
    if fcntl is None:
        yield
        return
    lock_path = root / "meta" / ".tidy.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def apply_plan(root: Path | str, plan: TidyPlan) -> dict[str, Any]:
    """校验并执行计划，同时保存执行前的 Note 快照。

    执行前会重新校验计划，并确认每个目标 Note 的 ``content_hash`` 仍与操作
    中的 ``expected_revision`` 一致；操作未提供该值时改用
    ``source_snapshot``。同一 ``plan_id`` 重跑时会替换 Note 快照并重写
    ``plan.json``，已有 ``report.json`` 只在本次操作全部完成后重写。仅操作
    循环中的异常会触发 ``notes`` 恢复；报告写入失败不会撤销已执行的变更。
    本函数不自行获取 Tidy 锁。

    Args:
        root: 记忆目录。
        plan: 已生成的整理计划。

    Returns:
        计划 ID、已处理目标和操作总数。

    Raises:
        ValueError: 计划无效、目标在执行前缺失，或 ``content_hash`` 与计划记录
            不一致。
        Exception: 快照、Note 更新、恢复或报告写入失败时透传底层异常。
    """
    root_path = Path(root)
    errors = validate_plan(root_path, plan)
    if errors:
        raise ValueError(f"invalid plan: {errors}")

    store = MemoryStore(root_path)
    id_map = _memory_id_to_stem(root_path)

    for op in plan.operations:
        stem = id_map[op.target]
        note = store.load_note(stem)
        if note is None:
            raise ValueError(f"stale: target {op.target} vanished before apply")
        expected = op.expected_revision or plan.source_snapshot.get(op.target)
        if expected and note.content_hash != expected:
            raise ValueError(
                f"stale: {op.target} changed (expected {expected}, "
                f"got {note.content_hash})"
            )

    snap_dir = root_path / _SNAPSHOTS_DIR / plan.plan_id
    snap_dir.parent.mkdir(parents=True, exist_ok=True)
    notes_src = root_path / "notes"
    snap_notes = snap_dir / "notes"
    if snap_notes.exists():
        shutil.rmtree(snap_notes)
    if notes_src.exists():
        shutil.copytree(notes_src, snap_notes)
    (snap_dir / "plan.json").write_text(
        json.dumps(_plan_to_dict(plan), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    applied: list[str] = []
    try:
        for op in plan.operations:
            stem = id_map[op.target]
            if op.type == "keep":
                applied.append(op.target)
                continue
            if op.type == "retire":
                store.update_note_fields(stem, {"status": "retired"})
            elif op.type == "revise":
                safe = {
                    key: value
                    for key, value in op.new_fields.items()
                    if key in _REVISE_ALLOWED_FIELDS
                }
                store.update_note_fields(stem, safe)
            elif op.type in ("supersede", "contradict"):
                store.update_note_fields(
                    stem,
                    {
                        "status": (
                            "superseded" if op.type == "supersede" else "contradicted"
                        ),
                        "superseded_by": op.by,
                    },
                )
                replacer_stem = id_map[op.by]
                replacer = store.load_note(replacer_stem)
                if replacer is not None:
                    new_super = tuple(sorted(set(replacer.supersedes) | {op.target}))
                    store.update_note_fields(replacer_stem, {"supersedes": new_super})
            elif op.type == "merge_sources":
                store.update_note_fields(
                    stem,
                    {"status": "superseded", "superseded_by": op.canonical},
                )
                canon_stem = id_map[op.canonical]
                canon = store.load_note(canon_stem)
                target = store.load_note(stem)
                if canon is not None and target is not None:
                    merged = tuple(sorted(set(canon.sources) | set(target.sources)))
                    store.update_note_fields(canon_stem, {"sources": merged})
            applied.append(op.target)
    except Exception:
        notes_dst = root_path / "notes"
        if (snap_dir / "notes").exists():
            if notes_dst.exists():
                shutil.rmtree(notes_dst)
            shutil.copytree(snap_dir / "notes", notes_dst)
        raise

    report = {
        "plan_id": plan.plan_id,
        "applied": applied,
        "operations": len(plan.operations),
    }
    (snap_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def rollback_plan(root: Path | str, plan_id: str) -> None:
    """用指定计划的执行前快照替换当前 ``notes``。

    若当前 ``notes`` 存在，先将它改名为 ``notes.trash``。复制快照失败时会
    删除未完成的新目录，并在存在旧目录时将其改回 ``notes``；复制成功后
    删除旧目录。本函数只恢复 Note 文件，不恢复报告或其他派生产物，也不
    自行获取 Tidy 锁。

    Args:
        root: 记忆目录。
        plan_id: 要恢复的计划 ID。

    Raises:
        FileNotFoundError: 快照中没有 ``notes`` 目录。
        Exception: 目录改名、复制、恢复或清理失败时透传底层异常。
    """
    snap_dir = Path(root) / _SNAPSHOTS_DIR / plan_id
    notes_backup = snap_dir / "notes"
    if not notes_backup.exists():
        raise FileNotFoundError(f"no snapshot for plan {plan_id!r}")
    notes_dst = Path(root) / "notes"
    trash = notes_dst.with_name(notes_dst.name + ".trash")
    if notes_dst.exists():
        notes_dst.rename(trash)
    try:
        shutil.copytree(notes_backup, notes_dst)
    except Exception:
        if notes_dst.exists():
            shutil.rmtree(notes_dst)
        if trash.exists():
            trash.rename(notes_dst)
        raise
    if trash.exists():
        shutil.rmtree(trash)


def _plan_to_dict(plan: TidyPlan) -> dict[str, Any]:
    """把计划展开为快照 JSON 使用的字典。

    该转换不重新校验计划；操作证据和核心候选会转成列表，其余字段沿用原值。

    Args:
        plan: 要序列化的整理计划。

    Returns:
        保留全部计划字段和操作顺序的字典。
    """
    return {
        "plan_id": plan.plan_id,
        "source_snapshot": plan.source_snapshot,
        "operations": [
            {
                "type": op.type,
                "target": op.target,
                "reason": op.reason,
                "evidence": list(op.evidence),
                "expected_revision": op.expected_revision,
                "canonical": op.canonical,
                "by": op.by,
                "new_fields": op.new_fields,
            }
            for op in plan.operations
        ],
        "dictionary_rebuild_required": plan.dictionary_rebuild_required,
        "core_candidates": list(plan.core_candidates),
    }
