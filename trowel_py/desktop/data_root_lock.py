"""在 Desktop sidecar 生命周期内独占一个 Trowel 数据根。"""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import IO


class DataRootInUseError(RuntimeError):
    """表示另一个 sidecar 或迁移进程已经持有目标数据根。"""


def _lock_handle(path: Path) -> IO[str]:
    """以仅当前用户可读写的模式打开数据根锁文件。"""

    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    return os.fdopen(descriptor, "r+", encoding="utf-8")


@contextmanager
def hold_data_root_lock(data_root: Path, *, owner: str) -> Iterator[Path]:
    """在调用方生命周期内非阻塞独占目标数据根。

    Args:
        data_root: 当前 sidecar 或迁移器准备读写的业务数据根。
        owner: 写入锁文件的运行模式，用于诊断占用来源。

    Yields:
        当前持有的锁文件路径。

    Raises:
        DataRootInUseError: 另一个进程已经持有同一锁文件。
    """

    data_root.mkdir(parents=True, exist_ok=True)
    lock_path = data_root / ".data-root.lock"
    handle = _lock_handle(lock_path)
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DataRootInUseError(
                f"Trowel data root is already in use: {data_root}"
            ) from exc
        handle.seek(0)
        handle.truncate()
        json.dump(
            {
                "version": 1,
                "owner": owner,
                "pid": os.getpid(),
                "acquired_at": datetime.now().isoformat(timespec="seconds"),
            },
            handle,
        )
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        yield lock_path
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
