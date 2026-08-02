"""提供 Memory CLI 的 Dictionary 一致性维护、daily review 与整理任务分发、Episode 修复和旧会话水位回填。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trowel_py.memory.hooks import HookRegistry
    from trowel_py.memory.sessions_repo import SessionsRepository


def ensure_dict_after_batch(root: Path) -> None:
    """批量修改 Note 后检查 Dictionary，并在不一致时尝试重建。

    Provider 无法创建时把漂移的 Dictionary 标为 stale；重建失败时保留该状态，
    两种情况都输出原因供后续任务重试。

    Args:
        root: 要检查的 Memory 根目录。
    """
    from trowel_py.memory.dictionary import (
        ensure_dictionary_consistent,
        mark_dictionary_stale_if_drifted,
    )

    try:
        from trowel_py.config import load_llm_config
        from trowel_py.llm.client import AnthropicProvider

        provider = AnthropicProvider(load_llm_config())
    except Exception as exc:  # noqa: BLE001 - CLI 需记录任意 provider 初始化失败。
        out = mark_dictionary_stale_if_drifted(root)
        print(
            f"[memory] dictionary: {out['dictionary_status']} "
            f"(no provider: {exc}; will retry)"
        )
        return
    try:
        out = ensure_dictionary_consistent(root, provider)
    except Exception as exc:  # noqa: BLE001 - 批处理已完成，重建失败留待后续重试。
        print(f"[memory] dictionary rebuild skipped: {exc}")
        return
    print(f"[memory] dictionary: {out['dictionary_status']}")


def run_memory_review(registry: HookRegistry, root: Path, date_str: str) -> int:
    """登记并同步执行一次 Memory daily review 写入任务。

    Args:
        registry: 用于登记任务和记录本次派发的 hook registry。
        root: 写入任务使用的 Memory 根目录。
        date_str: 本次 review 工作目录和备用日期标签，格式为 ``YYYY-MM-DD``；
            不限制会话登记日期。

    Returns:
        同步派发未抛出异常时返回 0；review 因锁冲突而跳过时也返回 0。
    """
    from trowel_py.memory.review_job import run_daily_review_sync

    registry.register_write_job(run_daily_review_sync)
    registry.dispatch_write_job({"date": date_str, "root": str(root)})
    print(
        f"[memory] review dispatched over {root} for {date_str} | "
        f"log: {registry.dispatch_log}"
    )
    return 0


def run_memory_tidy(registry: HookRegistry, root: Path) -> int:
    """同步分发 registry 中已登记的全部 Memory 整理任务。

    Args:
        registry: 保存整理任务和本次派发记录的 hook registry。
        root: 作为事件参数传给各整理任务的 Memory 根目录。

    Returns:
        所有已登记任务完成后返回 0；未登记任务时也返回 0。
    """
    registry.dispatch_tidy_job({"root": str(root)})
    print(
        f"[memory] tidy dispatched over {root} | "
        f"registered jobs: {len(registry._tidy)} | "
        f"log: {registry.dispatch_log}"
    )
    return 0


def run_repair(root: Path, date_str: str, *, apply: bool) -> int:
    """预览或执行修复：用仍存在的 draft 重建各会话的 Episode。

    Args:
        root: 要扫描或修复的 Memory 根目录。
        date_str: 要修复的日期，格式为 ``YYYY-MM-DD``。
        apply: ``False`` 时不写 Episode 或重建日记，只输出计划；``True`` 时
            先备份，再写入 Episode 并重建当日日记。

    Returns:
        报告输出后返回 0；写入后的 Episode 数与可用 draft 数不符时返回 1。
    """
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
    """返回 JSONL 当前字节数；路径为空、缺失或不是文件时返回 ``None``。

    预览和回填时都直接读取文件，回填不会沿用此前预览输出的字节数。

    Args:
        path_value: JSONL 路径；空字符串表示会话没有源文件。
    """
    if not path_value:
        return None
    path = Path(path_value)
    return path.stat().st_size if path.is_file() else None


def _apply_backfill(
    repo: SessionsRepository,
    plan: list[tuple[str, str, str | None]],
) -> None:
    """按 JSONL 当前大小回填完成水位；旧任务已提炼完整会话时，同时将提炼水位推进到相同位置。

    Args:
        repo: 要更新的会话仓库。
        plan: ``(session_id, jsonl_path, extracted_at)`` 元组列表；
            ``extracted_at`` 非空表示旧任务已完成整会话提炼。
    """
    backfilled = 0
    already_extracted = 0
    skipped = 0
    for session_id, jsonl_path, extracted_at in plan:
        size = _jsonl_size(jsonl_path)
        if size is None:
            skipped += 1
            continue
        repo.claude.update_completed(session_id, size)
        if extracted_at:
            # 旧任务已提炼完整会话，提炼水位也要追平，避免重复处理。
            repo.claude.advance_segment(session_id, size, when=extracted_at)
            already_extracted += 1
        backfilled += 1
    print(f"  backfilled: {backfilled}")
    if already_extracted:
        print(f"    (of which already-extracted by 040-a: {already_extracted})")
    print(f"  skipped (jsonl missing): {skipped}")


def _print_backfill_plan(plan: list[tuple[str, str, str | None]]) -> None:
    """输出完成水位回填计划，标出已提炼会话和缺失的 JSONL。

    Args:
        plan: ``(session_id, jsonl_path, extracted_at)`` 元组列表。
    """
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
    """预览或回填指定日期旧会话的完成水位。

    只处理尚无完成水位的会话，并在 review 独占锁内读取计划和执行写入。

    Args:
        root: 会话数据库所在的 Memory 根目录。
        date_str: 要处理的登记日期，格式为 ``YYYY-MM-DD``。
        apply: ``False`` 时不回填会话水位，只输出计划；``True`` 时按各 JSONL
            当前字节数写入。

    Returns:
        计划或执行结果输出后返回 0；review 任务持有锁时跳过并返回 0。
    """
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
                    for record in repo.claude.find_by_date(date_str)
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
