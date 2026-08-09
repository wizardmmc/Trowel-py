"""防止并发质量入口互相覆盖 moon 的单一原始报告和状态日志。"""

from __future__ import annotations

import fcntl
from pathlib import Path
from types import TracebackType
from typing import TextIO


class QualityRunLock:
    """在 moon 执行和证据复制期间持有仓库级排他文件锁。"""

    def __init__(self, path: Path) -> None:
        """绑定不进入 Git 的锁文件路径。

        Args:
            path: 同一 moon workspace 的所有质量入口共同使用的锁文件。
        """
        self._path = path
        self._handle: TextIO | None = None

    def __enter__(self) -> QualityRunLock:
        """等待并取得排他锁。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._path.open("a+", encoding="utf-8")
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """释放排他锁并关闭文件句柄。

        Args:
            exc_type: ``with`` 块异常类型；正常结束时为 None。
            exc_value: ``with`` 块异常对象；正常结束时为 None。
            traceback: ``with`` 块异常调用栈；正常结束时为 None。
        """
        if self._handle is None:
            return
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None
