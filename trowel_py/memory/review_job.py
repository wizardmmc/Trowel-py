"""提供 Daily review 入口，并重新导出 ``run_one_session`` 与 ``DistillError``。"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

from trowel_py.memory.daily_review.agent import (
    DistillError as DistillError,
    HostFactory,
    run_one_session as run_one_session,
)
from trowel_py.memory.daily_review.batch import (
    run_daily_review_locked as _run_daily_review_locked,
)
from trowel_py.memory.paths import resolve_memory_root
from trowel_py.resource_lifecycle.registry import ResourceRegistry

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _review_lock(root: Path) -> Iterator[None]:
    """尝试独占同一 Memory 根目录的 review 进程锁。

    支持 ``flock`` 时以非阻塞方式锁定 ``meta/.review.lock``；锁文件会保留，
    正常退出或上下文主体抛错时会尝试解锁再关闭 fd。导入 ``fcntl`` 失败时
    不加锁，由调用方保证不会并发执行。竞争失败会先关闭 fd 再重新抛出；
    首次 flock 的其他错误或解锁错误直接传播，当前实现可能来不及关闭 fd。

    Args:
        root: 要互斥处理的 Memory 根目录。

    Yields:
        锁已取得时进入受保护的上下文；无法导入 ``fcntl`` 时直接进入。

    Raises:
        BlockingIOError: 另一进程已经持有该锁。
        OSError: 无法创建、打开或操作锁文件。
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
    eligible_before: str | None = None,
    review_session_id: str | None = None,
    resource_registry: ResourceRegistry | None = None,
    allow_default_provider: bool = True,
) -> None:
    """提炼所有已完成但尚未推进 extracted 水位的增量 segment。

    并发调用无法取得锁时直接跳过。``date_str`` 只作为 review workdir 和
    fallback 日期标签，不限制待处理 session 的注册日期。``review_session_id``
    存在时只处理该关闭请求对应的 Trowel 会话。
    """
    root = Path(memory_root) if memory_root is not None else resolve_memory_root()
    if date_str is None:
        if event and isinstance(event, dict) and event.get("date"):
            date_str = str(event["date"])
        else:
            date_str = date.today().isoformat()
    if eligible_before is None and event and isinstance(event, dict):
        raw_cutoff = event.get("eligible_before")
        if raw_cutoff:
            eligible_before = str(raw_cutoff)
    if review_session_id is None and event and isinstance(event, dict):
        raw_session_id = event.get("review_session_id")
        if raw_session_id:
            review_session_id = str(raw_session_id)
    if resource_registry is None and event and isinstance(event, dict):
        candidate_registry = event.get("_resource_registry")
        if isinstance(candidate_registry, ResourceRegistry):
            resource_registry = candidate_registry
    if host_factory is None and resource_registry is not None:
        from trowel_py.memory.daily_review.agent import resource_aware_host_factory

        host_factory = resource_aware_host_factory(resource_registry)
    if not allow_default_provider and provider is None:
        from trowel_py.memory.daily_review.batch import NO_LLM_PROVIDER

        provider = NO_LLM_PROVIDER
    try:
        with _review_lock(root):
            await _run_daily_review_locked(
                root,
                date_str,
                host_factory,
                provider,
                eligible_before,
                review_session_id,
            )
    except BlockingIOError:
        logger.warning("daily review already running; skipping this run")


def run_daily_review_sync(event: Any = None) -> None:
    """为同步 hook 运行异步 daily review。"""
    import asyncio

    root = None
    date_str = None
    eligible_before = None
    review_session_id = None
    resource_registry = None
    host_factory = None
    provider = None
    allow_default_provider = True
    if event and isinstance(event, dict):
        root = event.get("root")
        date_str = event.get("date")
        eligible_before = event.get("eligible_before")
        review_session_id = event.get("review_session_id")
        candidate_registry = event.get("_resource_registry")
        if isinstance(candidate_registry, ResourceRegistry):
            resource_registry = candidate_registry
        candidate_host_factory = event.get("_host_factory")
        if callable(candidate_host_factory):
            host_factory = candidate_host_factory
        if event.get("_provider_resolved") is True:
            provider = event.get("_provider")
            allow_default_provider = False
    root_path = Path(root) if root else None
    asyncio.run(
        run_daily_review(
            event,
            memory_root=root_path,
            date_str=date_str,
            eligible_before=eligible_before,
            review_session_id=review_session_id,
            resource_registry=resource_registry,
            host_factory=host_factory,
            provider=provider,
            allow_default_provider=allow_default_provider,
        )
    )
