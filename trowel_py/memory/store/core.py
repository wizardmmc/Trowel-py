"""读取 Memory 根目录下的 Core 与 L0 Dictionary 文件。"""

from __future__ import annotations

from pathlib import Path

from trowel_py.memory.types import CoreItem

from .codec import _core_item_from_dict, _split_frontmatter

_CORE_FILE = "core.md"
_DICT_L0 = "dictionary-L0.md"


class _CoreStore:
    """为 ``MemoryStore`` 提供 Core 与 L0 Dictionary 的只读能力。

    组合后的仓储必须提供 ``root``。读取不会创建文件；I/O 和 UTF-8 解码错误
    直接传播。
    """

    root: Path

    def load_core(self) -> str:
        """以 UTF-8 读取 ``core.md`` 全文，文件不存在时返回空字符串。"""

        path = self.root / _CORE_FILE
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def load_dictionary_L0(self) -> str:
        """以 UTF-8 读取 ``dictionary-L0.md`` 全文，文件不存在时返回空字符串。"""

        path = self.root / _DICT_L0
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def load_core_items(self) -> tuple[CoreItem, ...]:
        """从 ``core.md`` frontmatter 的 ``items`` 列表解析 Core 条目。

        文件缺失、frontmatter 无效或 ``items`` 不是列表时返回空元组；列表中的
        非映射元素会被跳过。字段值由编解码层转换，不在此处执行 schema 校验。
        """

        path = self.root / _CORE_FILE
        if not path.exists():
            return ()
        fm, _body = _split_frontmatter(path.read_text(encoding="utf-8"))
        if not fm:
            return ()
        items = fm.get("items")
        if not isinstance(items, list):
            return ()
        return tuple(
            core_item
            for it in items
            if (core_item := _core_item_from_dict(it)) is not None
        )
