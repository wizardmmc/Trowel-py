"""按独立水位批量提炼已完成会话的 Profile 建议。"""

from __future__ import annotations

import contextlib
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - 非 Unix 平台
    fcntl = None  # type: ignore[assignment]

from trowel_py.memory.paths import resolve_memory_root
from trowel_py.profile.distill.adapters.claude import (
    build_claude_backlog,
)
from trowel_py.profile.distill.adapters.codex import (
    build_codex_backlog,
)
from trowel_py.profile.distill.agent import HostFactory
from trowel_py.profile.distill.gate import DistillError
from trowel_py.profile.distill.models import ProfileDistillCandidate
from trowel_py.profile.distill.processor import process_profile_source
from trowel_py.profile.distill.state import (
    load_codex_processed,
    load_processed,
)
from trowel_py.profile.suggestions import append_suggestions
from trowel_py.memory.sessions_repo import (
    create_sessions_repository,
    open_sessions_db,
)

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _distill_lock(root: Path):
    """在上下文期间非阻塞持有 Profile 提炼进程锁。

    支持 ``flock`` 时锁定 ``<root>/meta/.distill.lock``；锁文件会保留，退出
    上下文时仅解锁并关闭描述符。其他平台直接进入上下文，不提供并发保护。

    Args:
        root: 用于定位锁文件的 Memory 根目录。

    Raises:
        BlockingIOError: 另一个进程已经持有锁。
        OSError: 无法创建、打开、加锁或释放锁文件。
    """
    if fcntl is None:
        yield
        return
    lock_path = root / "meta" / ".distill.lock"
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


async def run_daily_distill(
    memory_root: Path | None,
    proxy_base_url: str,
    *,
    settings_path: Path | str | None = None,
    host_factory: HostFactory | None = None,
    date_str: str | None = None,
) -> None:
    """串行提炼有新内容的 Claude 会话和 Codex turns。"""
    root = memory_root if memory_root is not None else resolve_memory_root()
    if date_str is None:
        date_str = datetime.now().date().isoformat()
    try:
        with _distill_lock(root):
            await _run_daily_distill_locked(
                root, proxy_base_url, settings_path, host_factory, date_str
            )
    except BlockingIOError:
        logger.warning("profile distill already running; skipping this run")


async def _run_daily_distill_locked(
    root: Path,
    proxy_base_url: str,
    settings_path: Path | str | None,
    host_factory: HostFactory | None,
    date_str: str,
) -> None:
    """按完成顺序串行提炼 Claude 字节区间和 Codex turns。

    两种 runtime 各自把原始记录适配为共同候选和 ``context/target`` 来源。
    ``DistillError`` 不推进当前水位；Codex 某 turn 失败后，本轮不再越过同
    thread 的后续 turns。其他来源仍可继续。门禁成功即使没有建议也会推进；
    有建议时先追加队列，再写独立 Profile 水位。两步没有事务，队列成功而
    水位失败时重试仍会再次处理该来源。其他异常中止剩余来源。

    Args:
        root: Memory 根目录；用于 sessions 数据库、建议队列和独立水位。
        proxy_base_url: 传给单会话提炼的代理地址。
        settings_path: 传给单会话提炼的 provider settings 路径。
        host_factory: 可选的测试或替代 host 构造器。
        date_str: 新建议和队列使用的日期。

    Raises:
        OSError: 无法访问数据库、来源、队列或水位文件。
        ValueError: 独立水位或其他持久化数据无法解析。
    """

    conn = open_sessions_db(root)
    try:
        repo = create_sessions_repository(conn)
        claude_candidates = repo.claude.find_all_completed_sessions()
        codex_candidates = repo.codex.list_completed_user_turns()
        backlog: list[ProfileDistillCandidate] = [
            *build_claude_backlog(claude_candidates, load_processed(root)),
            *build_codex_backlog(
                codex_candidates,
                load_codex_processed(root),
            ),
        ]
        backlog.sort(
            key=lambda candidate: (
                candidate.completed_at,
                candidate.registered_at,
                candidate.source_id,
            )
        )
        logger.info(
            "profile distill: %d Claude candidate(s), %d Codex candidate(s),"
            " %d pending source(s) (date_str=%s)",
            len(claude_candidates),
            len(codex_candidates),
            len(backlog),
            date_str,
        )
        blocked_sequences: set[str] = set()
        for candidate in backlog:
            if candidate.sequence_id in blocked_sequences:
                continue
            try:
                suggestions = await process_profile_source(
                    candidate.build_source(),
                    date_str,
                    root,
                    proxy_base_url=proxy_base_url,
                    settings_path=settings_path,
                    host_factory=host_factory,
                )
            except DistillError as exc:
                blocked_sequences.add(candidate.sequence_id)
                logger.warning(
                    "profile distill failed for %s (skipped, not marked): %s",
                    candidate.label,
                    exc,
                )
                continue
            if suggestions:
                append_suggestions(root, suggestions, updated=date_str)
                logger.info(
                    "profile distill: +%d suggestion(s) from %s",
                    len(suggestions),
                    candidate.label,
                )
            candidate.mark_processed(
                root,
                at=datetime.now().isoformat(),
            )
    finally:
        conn.close()


def run_daily_distill_sync(event: Any = None) -> None:
    """把 scheduler event 映射为异步批处理参数。"""
    import asyncio

    root = None
    date_str = None
    proxy_base_url = ""
    settings_path = None
    if event and isinstance(event, dict):
        root = event.get("root")
        date_str = event.get("date")
        proxy_base_url = event.get("proxy_base_url", "")
        settings_path = event.get("settings_path")
    root_path = Path(root) if root else None
    asyncio.run(
        run_daily_distill(
            root_path,
            proxy_base_url,
            settings_path=settings_path,
            date_str=date_str,
        )
    )
