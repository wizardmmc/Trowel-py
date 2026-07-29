"""从幸存 draft 回填 episode，并重建指定日期的 daily。

修复不重新运行 agent，也不使用 draft 中的 notes。默认 dry-run，但扫描会以
可写方式打开并按需创建或迁移 sessions 数据库；apply 在扫描完成后备份当时
仍存在的 memory root，随后才写 episode 和 daily。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from trowel_py.memory.draft import parse_draft
from trowel_py.memory.daily_review.workspace import review_workdir_root
from trowel_py.memory.sessions_repo import (
    create_sessions_repository,
    open_sessions_db,
)
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import PersistContext


@dataclass(frozen=True)
class RepairPlan:
    """扫描到的一份会话修复输入。

    Attributes:
        cc_session_id: draft 所在会话目录的名称。
        has_draft: ``draft.json`` 是否成功读取并解析。
        has_session_record: 请求日期的 sessions 登记中是否有同标识记录。
        diary_dates: 可解析 draft 中全部 diary 条目的日期，保留原顺序。
    """

    cc_session_id: str
    has_draft: bool
    has_session_record: bool
    diary_dates: tuple[str, ...]


@dataclass(frozen=True)
class RepairReport:
    """一次 memory 修复的扫描与执行报告。

    Attributes:
        date: 请求重建的 daily 日期。
        applied: 是否执行了回填，而非仅扫描。
        backup_dir: dry-run 时为空；apply 时为选定路径，备份阶段根目录已不存在
            时该目录不会创建。
        planned: 扫描到的 draft 计划，包括发生约定读取或解析错误的文件。
        missing_drafts: 有 sessions 登记、但扫描时没有 ``draft.json`` 的会话标识。
        episodes_created: 成功调用 episode upsert 的 draft 数，并非新文件数量。
        daily_rebuilt: 是否找到该日期的 episode 内容并重写 daily。
        notes_before: 扫描时 ``notes/*.md`` 的文件数量。
    """

    date: str
    applied: bool
    backup_dir: str | None
    planned: tuple[RepairPlan, ...]
    missing_drafts: tuple[str, ...]
    episodes_created: int
    daily_rebuilt: bool
    notes_before: int

    @property
    def ok(self) -> bool:
        """dry-run 恒为真；apply 时仅表示 upsert 数等于扫描时可解析 draft 数。"""
        if not self.applied:
            return True
        return self.episodes_created == sum(1 for p in self.planned if p.has_draft)


def _scan(memory_root: Path, date_str: str) -> tuple[list[RepairPlan], list[str], dict]:
    """扫描指定日期的 draft 与 sessions 登记。

    sessions 数据库会以可写方式打开并按需初始化。每个存在的 ``draft.json``
    都生成计划；读取或解析产生 ``ValueError``、``OSError``、
    ``AttributeError`` 或 ``TypeError`` 时记为 ``has_draft=False``，但不再
    算作“缺少 draft”。其他异常直接传播。返回计划、缺少文件的已登记会话
    标识，以及登记记录映射。
    """
    review_root = review_workdir_root(memory_root) / date_str
    conn = open_sessions_db(memory_root)
    try:
        repo = create_sessions_repository(conn)
        sessions = {s.cc_session_id: s for s in repo.find_by_date(date_str)}
    finally:
        conn.close()

    plans: list[RepairPlan] = []
    draft_sids: set[str] = set()
    if review_root.exists():
        for dp in sorted(review_root.glob("*/draft.json")):
            sid = dp.parent.name
            draft_sids.add(sid)
            try:
                draft = parse_draft(dp.read_text(encoding="utf-8"))
                diary_dates = tuple(d.date for d in draft.diary)
                plans.append(
                    RepairPlan(
                        cc_session_id=sid,
                        has_draft=True,
                        has_session_record=sid in sessions,
                        diary_dates=diary_dates,
                    )
                )
            except (ValueError, OSError, AttributeError, TypeError):
                # 约定的读取或解析错误只记为不可用，不重新运行 agent 生成替代内容。
                plans.append(
                    RepairPlan(
                        cc_session_id=sid,
                        has_draft=False,
                        has_session_record=sid in sessions,
                        diary_dates=(),
                    )
                )
    missing = [sid for sid in sessions if sid not in draft_sids]
    return plans, missing, sessions


def _unique_backup_path(memory_root: Path, date_str: str) -> Path:
    """选择当前不存在的相邻备份路径，同秒冲突时从 ``-2`` 开始递增。

    本函数不创建或锁定路径；并发调用仍可能选中同一名称。
    """
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = memory_root.parent / f"memory.bak-repair-{date_str}-{ts}"
    n = 2
    while base.exists():
        base = memory_root.parent / f"memory.bak-repair-{date_str}-{ts}-{n}"
        n += 1
    return base


def repair_memory(
    memory_root: Path, date_str: str, *, apply: bool = False
) -> RepairReport:
    """扫描幸存 draft，并按需回填 episode 和指定日期的 daily。

    dry-run 不写 episode、daily 或备份，但扫描可能创建或迁移 sessions 数据库。
    apply 的备份阶段仅在 ``memory_root.exists()`` 时复制；扫描通常已经创建或
    迁移 sessions 数据库，所以调用前不存在的根目录也可能在此时被备份。可解析
    draft 按会话写入固定 ``sid:0:end`` segment，因而重复执行是 upsert。读取
    或解析产生 ``ValueError``、``OSError``、``AttributeError`` 或
    ``TypeError`` 的 draft 被跳过，其他异常传播；缺少登记的 draft 仍会以空
    来源元数据写入。最后聚合全部 episode，仅在请求日期有内容时重写 daily，
    无内容则保留现有 daily。整个流程失败时不会自动从备份回滚。

    Args:
        memory_root: 待修复的 memory 根目录。
        date_str: 用于数据库查询和路径拼接的可信 ``YYYY-MM-DD``；本函数不校验。
        apply: 是否真正备份并写入；默认为仅扫描。

    Returns:
        扫描结果、备份路径和写入计数。

    Raises:
        AssertionError: 断言启用且 apply 后 ``notes/*.md`` 的非递归净文件数
            发生变化。
    """
    plans, missing, sessions = _scan(memory_root, date_str)
    notes_before = (
        len(list((memory_root / "notes").glob("*.md")))
        if (memory_root / "notes").exists()
        else 0
    )

    if not apply:
        return RepairReport(
            date=date_str,
            applied=False,
            backup_dir=None,
            planned=tuple(plans),
            missing_drafts=tuple(missing),
            episodes_created=0,
            daily_rebuilt=False,
            notes_before=notes_before,
        )

    # 串行 apply 为现有根目录选择独立快照路径；并发时名称仍可能冲突。
    backup = _unique_backup_path(memory_root, date_str)
    if memory_root.exists():
        shutil.copytree(memory_root, backup)

    store = MemoryStore(memory_root)
    review_root = review_workdir_root(memory_root) / date_str
    created = 0
    if review_root.exists():
        for dp in sorted(review_root.glob("*/draft.json")):
            sid = dp.parent.name
            try:
                draft = parse_draft(dp.read_text(encoding="utf-8"))
            except (ValueError, OSError, AttributeError, TypeError):
                continue
            s = sessions.get(sid)
            ctx = PersistContext(
                segment_id=f"{sid}:0:end",
                cc_session_id=sid,
                workdir=s.workdir if s else "",
                registered_at=s.registered_at if s else "",
                review_date=date_str,
                source_jsonl=s.jsonl_path if s else "",
            )
            store.write_episode(ctx, draft.diary)
            created += 1

    daily_date = store.derive_daily_from_episodes(date_str)
    notes_after = (
        len(list((memory_root / "notes").glob("*.md")))
        if (memory_root / "notes").exists()
        else 0
    )
    # 仅检查 notes/ 顶层 Markdown 的净数量；不检测内容、同数替换或递归变化，
    # 且 Python -O 会移除这条断言。
    assert notes_after == notes_before, "repair mutated notes — aborting"

    return RepairReport(
        date=date_str,
        applied=True,
        backup_dir=str(backup),
        planned=tuple(plans),
        missing_drafts=tuple(missing),
        episodes_created=created,
        daily_rebuilt=bool(daily_date),
        notes_before=notes_before,
    )
