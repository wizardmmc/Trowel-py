"""通过 Memory 公开门面读取指定本地根目录的统计事实。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from trowel_py.memory.statistics_facade import read_memory_statistics
from trowel_py.statistics.window import StatisticsWindow


class FileMemoryStatisticsReader:
    """读取一个 Memory 根目录，不持有数据库连接或写入水位。

    Attributes:
        root: Note、访问日志、判效报告和 Dictionary 状态所在的 Memory 根目录。
    """

    def __init__(self, root: Path | str) -> None:
        """保存后续查询使用的 Memory 根目录。

        Args:
            root: 要读取的隔离或正式 Memory 根目录。
        """
        self.root = Path(root)

    def read(self, window: StatisticsWindow) -> dict[str, Any]:
        """读取查询窗内的 Memory 使用事实和当前资产快照。

        Args:
            window: 已按调用方时区解析的半开时间窗。

        Returns:
            Memory 公开门面生成的不含正文和身份标识的快照。
        """
        return read_memory_statistics(
            self.root,
            window_start=window.start,
            window_end=window.end,
            local_tz=window.start.tzinfo,
        )
