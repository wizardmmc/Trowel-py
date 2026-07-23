from __future__ import annotations

import contextlib
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

from trowel_py.memory._review_agent import (
    DistillError as DistillError,
    HostFactory,
    run_one_session as run_one_session,
)
from trowel_py.memory._review_batch import (
    run_daily_review_locked as _run_daily_review_locked,
)
from trowel_py.memory.paths import resolve_memory_root

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _review_lock(root: Path):
    """阻止定时任务与手动 review 同时处理同一 memory root。

    非 Unix 平台没有 ``flock``，此处保持原有 no-op 行为，由调用方保证单实例。
    """
    if fcntl is None:
        yield
        return
    lock_path = root / "meta" / ".review.lock"
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


async def run_daily_review(
    event: Any = None,
    memory_root: Path | None = None,
    date_str: str | None = None,
    *,
    host_factory: HostFactory | None = None,
    provider: Any = None,
) -> None:
    """提炼所有已完成但尚未推进 extracted 水位的增量 segment。

    并发调用无法取得锁时直接跳过。``date_str`` 只作为 review workdir 和
    fallback 日期标签，不限制待处理 session 的注册日期。
    """
    root = Path(memory_root) if memory_root is not None else resolve_memory_root()
    if date_str is None:
        if event and isinstance(event, dict) and event.get("date"):
            date_str = str(event["date"])
        else:
            date_str = date.today().isoformat()
    try:
        with _review_lock(root):
            await _run_daily_review_locked(root, date_str, host_factory, provider)
    except BlockingIOError:
        logger.warning("daily review already running; skipping this run")


def run_daily_review_sync(event: Any = None) -> None:
    """为同步 hook 运行异步 daily review。"""
    import asyncio

    root = None
    date_str = None
    if event and isinstance(event, dict):
        root = event.get("root")
        date_str = event.get("date")
    root_path = Path(root) if root else None
    asyncio.run(run_daily_review(event, memory_root=root_path, date_str=date_str))
