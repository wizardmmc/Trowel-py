"""Memory CLI 的批处理维护操作。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trowel_py.memory.hooks import HookRegistry
    from trowel_py.memory.sessions_repo import SessionsRepository


def ensure_dict_after_batch(root: Path) -> None:
    """批处理改动 note 后检查并收敛字典。"""
    from trowel_py.memory.dictionary import (
        ensure_dictionary_consistent,
        mark_dictionary_stale_if_drifted,
    )

    try:
        from trowel_py.config import load_llm_config
        from trowel_py.llm.client import AnthropicProvider

        provider = AnthropicProvider(load_llm_config())
    except Exception as exc:  # noqa: BLE001
        out = mark_dictionary_stale_if_drifted(root)
        print(
            f"[memory] dictionary: {out['dictionary_status']} "
            f"(no provider: {exc}; will retry)"
        )
        return
    try:
        out = ensure_dictionary_consistent(root, provider)
    except Exception as exc:  # noqa: BLE001
        print(f"[memory] dictionary rebuild skipped: {exc}")
        return
    print(f"[memory] dictionary: {out['dictionary_status']}")


def run_memory_review(registry: HookRegistry, root: Path, date_str: str) -> int:
    """分发单日写入任务。"""
    from trowel_py.memory.review_job import run_daily_review_sync

    registry.register_write_job(run_daily_review_sync)
    registry.dispatch_write_job({"date": date_str, "root": str(root)})
    print(
        f"[memory] review dispatched over {root} for {date_str} | "
        f"log: {registry.dispatch_log}"
    )
    return 0


def run_memory_tidy(registry: HookRegistry, root: Path) -> int:
    """分发已注册的整理任务。"""
    registry.dispatch_tidy_job({"root": str(root)})
    print(
        f"[memory] tidy dispatched over {root} | "
        f"registered jobs: {len(registry._tidy)} | "
        f"log: {registry.dispatch_log}"
    )
    return 0


def run_repair(root: Path, date_str: str, *, apply: bool) -> int:
    """从存活 draft 修复逐会话 episode。"""
    from trowel_py.memory.repair import repair_memory

    report = repair_memory(root, date_str, apply=apply)
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[memory] repair {mode} over {root} for {date_str}")
    print(f"  drafts found: {sum(1 for item in report.planned if item.has_draft)}")
    print(
        f"  missing drafts (session registered, no draft): {len(report.missing_drafts)}"
    )
    for session_id in report.missing_drafts:
        print(f"    - {session_id}")
    if apply:
        print(f"  episodes created: {report.episodes_created}")
        print(f"  daily rebuilt: {report.daily_rebuilt}")
        print(f"  backup: {report.backup_dir}")
        print(f"  notes unchanged: {report.notes_before}")
        if not report.ok:
            print("  VERIFICATION FAILED: episodes_created != draft count")
            return 1
    return 0


def _jsonl_size(path_value: str) -> int | None:
    """在写入前读取 JSONL 当前大小，避免使用过期计划值。"""
    if not path_value:
        return None
    path = Path(path_value)
    return path.stat().st_size if path.is_file() else None


def _apply_backfill(
    repo: SessionsRepository,
    plan: list[tuple[str, str, str | None]],
) -> None:
    backfilled = 0
    already_extracted = 0
    skipped = 0
    for session_id, jsonl_path, extracted_at in plan:
        size = _jsonl_size(jsonl_path)
        if size is None:
            skipped += 1
            continue
        repo.update_completed(session_id, size)
        if extracted_at:
            # 已提取会话的 extracted 水位必须同步，避免整段重复蒸馏。
            repo.advance_extracted(session_id, size, when=extracted_at)
            already_extracted += 1
        backfilled += 1
    print(f"  backfilled: {backfilled}")
    if already_extracted:
        print(f"    (of which already-extracted by 040-a: {already_extracted})")
    print(f"  skipped (jsonl missing): {skipped}")


def _print_backfill_plan(plan: list[tuple[str, str, str | None]]) -> None:
    skipped = 0
    for session_id, jsonl_path, extracted_at in plan:
        size = _jsonl_size(jsonl_path)
        marker = str(size) if size is not None else "MISSING"
        tag = " [040-a extracted]" if extracted_at else ""
        print(f"  - {session_id}: {marker}{tag}  ({jsonl_path})")
        if size is None:
            skipped += 1
    if skipped:
        print(f"  ({skipped} jsonl missing, would be skipped on apply)")


def run_backfill_completed(root: Path, date_str: str, *, apply: bool) -> int:
    """按 JSONL 当前大小回填旧会话完成水位。"""
    from trowel_py.memory.review_job import _review_lock
    from trowel_py.memory.sessions_repo import (
        create_sessions_repository,
        open_sessions_db,
    )

    try:
        with _review_lock(root):
            conn = open_sessions_db(root)
            try:
                repo = create_sessions_repository(conn)
                plan = [
                    (record.cc_session_id, record.jsonl_path, record.extracted_at)
                    for record in repo.find_by_date(date_str)
                    if record.last_completed_offset is None
                ]
                mode = "APPLY" if apply else "DRY-RUN"
                print(f"[memory] backfill-completed {mode} over {root} for {date_str}")
                print(f"  legacy rows needing backfill: {len(plan)}")
                if apply:
                    _apply_backfill(repo, plan)
                else:
                    _print_backfill_plan(plan)
                return 0
            finally:
                conn.close()
    except BlockingIOError:
        print(
            f"[memory] backfill-completed skipped (a review job is running) for {date_str}"
        )
        return 0
