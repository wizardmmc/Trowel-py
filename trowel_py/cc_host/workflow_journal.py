"""按字节增量读取 JSONL 文件中的完整行。"""

from __future__ import annotations

import os
from pathlib import Path


class JsonlCursor:
    """按文件身份和已提交字节偏移量读取新增的完整行。

    设备号或 inode 变化，以及文件长度小于已提交偏移量时，下一次读取从文件开头
    重新开始。游标只以 LF 划分行，不解码字节或解析 JSON。
    """

    def __init__(self) -> None:
        """创建尚未记录文件身份和已提交偏移量的游标。"""

        self._identity: tuple[int, int] | None = None
        self._offset = 0

    def read(self, path: Path) -> list[bytes]:
        """读取上次提交位置之后新增的完整行。

        只有读到 LF 才推进偏移量，未换行的尾部会在下次调用时重新读取。返回的
        每行移除末尾 LF，但保留 CR 和空行。

        Args:
            path: 要增量读取的 JSONL 文件。

        Returns:
            本次新增的完整行字节；没有完整新行时返回空列表。

        Raises:
            OSError: 文件打开、状态查询、定位或读取失败。
        """
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
