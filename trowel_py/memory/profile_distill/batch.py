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
from trowel_py.memory.profile_distill.agent import HostFactory, run_one_session
from trowel_py.memory.profile_distill.gate import DistillError
from trowel_py.memory.profile_distill.state import load_processed, mark_processed
from trowel_py.memory.profile_suggestions import append_suggestions
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)

logger = logging.getLogger("trowel_py.memory.profile_distill_job")


@contextlib.contextmanager
def _distill_lock(root: Path):
    """非阻塞持有 distill 进程锁；不支持 flock 的平台退化为无锁。"""
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
    """串行提炼有新内容的 session；失败项不推进独立水位。"""
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
    conn = open_sessions_db(root)
    try:
        repo = create_sessions_repository(conn)
        candidates = repo.find_all_completed_sessions()
        processed = load_processed(root)
        backlog: list[tuple[SessionRecord, int, int]] = []
        for session in candidates:
            end = session.last_completed_offset or 0
            start = (
                processed[session.cc_session_id].end_offset
                if session.cc_session_id in processed
                else 0
            )
            if end > start:
                backlog.append((session, start, end))
        logger.info(
            "profile distill: %d candidate(s), %d with new content (date_str=%s)",
            len(candidates),
            len(backlog),
            date_str,
        )
        for session, start, end in backlog:
            try:
                suggestions = await run_one_session(
                    session,
                    date_str,
                    root,
                    proxy_base_url=proxy_base_url,
                    settings_path=settings_path,
                    host_factory=host_factory,
                    start_offset=start or None,
                    end_offset=end,
                )
            except DistillError as exc:
                logger.warning(
                    "profile distill failed for %s (skipped, not marked): %s",
                    session.cc_session_id,
                    exc,
                )
                continue
            if suggestions:
                append_suggestions(root, suggestions, updated=date_str)
                logger.info(
                    "profile distill: +%d suggestion(s) from %s",
                    len(suggestions),
                    session.cc_session_id,
                )
            mark_processed(
                root,
                session.cc_session_id,
                end_offset=end,
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
