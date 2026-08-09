"""协调 Dictionary 索引读取与发布之间的 ``fcntl`` 文件锁。

发布路径在替换 L0/L1 并记录成功状态时持有排他锁，检查与搜索路径持有共享锁，
避免读取期间换代或多个发布相互交错。当前 Python 无法导入 ``fcntl`` 时，
此锁退化为空操作。
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - 当前 Python 不提供 fcntl
    fcntl = None  # type: ignore[assignment]

_DICT_LOCK_REL = "meta/.dictionary.lock"


@contextlib.contextmanager
def dictionary_lock(root: Path | str, *, exclusive: bool) -> Iterator[None]:
    """在支持 ``fcntl`` 的平台阻塞等待并持有 Dictionary 共享锁或排他锁。

    锁文件固定为 Memory 根目录下的 ``meta/.dictionary.lock``。调用 ``flock``
    时未使用 ``LOCK_NB``，因此会等待冲突锁释放；正常退出时先解锁再关闭文件
    描述符。当前 Python 无法导入 ``fcntl`` 时直接进入上下文，不提供互斥。

    Args:
        root: Dictionary 所在的 Memory 根目录。
        exclusive: True 时使用排他锁保护发布，False 时使用共享锁保护读取。

    Yields:
        已取得锁的受保护上下文；无法导入 ``fcntl`` 时则是不加锁的上下文。

    Raises:
        OSError: 无法创建、打开、加锁、解锁或关闭锁文件。加锁失败也会进入
            清理流程；解锁失败会跳过关闭，且解锁或关闭异常可能替代加锁异常
            或上下文内原有异常。
    """
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
