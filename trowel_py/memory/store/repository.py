"""组合文件型存储 mixin，提供统一的 ``MemoryStore`` 门面。"""

from __future__ import annotations

from pathlib import Path

from .core import _CoreStore
from .episodes import _EpisodeStore
from .notes import _NotesStore


class MemoryStore(_NotesStore, _EpisodeStore, _CoreStore):
    """汇集同一根目录下 Note、Episode、Diary 和 Core 的文件操作。

    Note、Episode 和 Diary 支持写入；Core 仅提供读取。
    构造时只保存路径，不创建目录、不解析为绝对路径，也不检查现有布局。

    Attributes:
        root: 所有 Memory 文件共同使用的根路径。
    """

    def __init__(self, root: Path | str) -> None:
        """把路径参数转换为 ``Path`` 并保存。

        Args:
            root: Memory 文件树的根路径，可为相对路径。
        """
        self.root = Path(root)
