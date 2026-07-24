"""协调字典读取与重建发布的 ``fcntl`` 文件锁。

发布路径持有排他锁，搜索与只读检查持有共享锁，避免读取期间替换 L1 文件或多个
重建同时发布。非 Unix 平台没有 ``flock``，此锁会降级为空操作。
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - 非 Unix 平台
    fcntl = None  # type: ignore[assignment]

_DICT_LOCK_REL = "meta/.dictionary.lock"


@contextlib.contextmanager
def dictionary_lock(root: Path | str, *, exclusive: bool):
    """发布使用 ``LOCK_EX``，读取使用 ``LOCK_SH``。"""
    if fcntl is None:
        yield
        return
    lock_path = Path(root) / _DICT_LOCK_REL
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
