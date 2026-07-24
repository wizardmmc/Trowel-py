"""提供与事件协议无关的 JSONL 文件游标。"""

from __future__ import annotations

import os
from pathlib import Path


class JsonlCursor:
    """按文件身份读取新增的完整行。"""

    def __init__(self) -> None:
        self._identity: tuple[int, int] | None = None
        self._offset = 0

    def read(self, path: Path) -> list[bytes]:
        """返回本次新增的完整行，未换行的尾部留待下次读取。"""
        with path.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            identity = (stat.st_dev, stat.st_ino)
            reset = identity != self._identity or stat.st_size < self._offset
            start = 0 if reset else self._offset
            stream.seek(start)
            chunk = stream.read()

        self._identity = identity
        complete_end = chunk.rfind(b"\n")
        if complete_end < 0:
            if reset:
                self._offset = 0
            return []

        self._offset = start + complete_end + 1
        return chunk[:complete_end].split(b"\n")
