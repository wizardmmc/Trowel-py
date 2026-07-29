"""读写并筛选日、周、月三层 Diary 文件。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from trowel_py.memory.schema import validate_entry
from trowel_py.memory.types import Diary

from .codec import _diary_from_fm, _dump_frontmatter, _split_frontmatter

_DIARY_DIR = "diary"
_LAYER_DIR = {"day": "daily", "week": "weekly", "month": "monthly"}


class _DiaryStore:
    """为 ``MemoryStore`` 提供 Diary 写入、读取和筛选能力。

    组合后的仓储必须提供 ``root``；I/O、UTF-8 和 YAML 序列化错误直接传播。
    """

    root: Path

    def write_diary(self, entry: dict[str, Any]) -> str:
        """校验并覆盖一份日、周或月 Diary。

        名称以 ``__`` 开头的字段不写入 frontmatter，``type`` 始终强制为
        ``diary``，``__body`` 作为正文。layer 决定 daily、weekly 或 monthly
        子目录；date 直接拼入目标路径且不做路径字符清理，同一路径的旧文件会
        被覆盖。

        Args:
            entry: 待写入的 frontmatter 字段和可选 ``__body``。date 只校验为
                非空字符串，不执行日期格式或路径字符清理。

        Returns:
            写入记录的 date 字段。

        Raises:
            ValueError: date、layer 或 promoted_knowledge 未通过 Diary schema。
        """

        fm = {k: v for k, v in entry.items() if not k.startswith("__")}
        fm["type"] = "diary"
        result = validate_entry("diary", fm)
        if not result.ok:
            raise ValueError(f"invalid diary: {result.errors}")
        layer = str(fm.get("layer", "day"))
        date = str(fm.get("date", ""))
        dir_name = _LAYER_DIR.get(layer, "daily")
        path = self.root / _DIARY_DIR / dir_name / f"{date}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _dump_frontmatter(fm, entry.get("__body", "")), encoding="utf-8"
        )
        return date

    def load_diary(
        self, since: str | None = None, layer: str | None = None
    ) -> list[Diary]:
        """递归读取 ``type=diary`` 的 Markdown，并按日期下界和层级筛选。

        ``since`` 与 frontmatter 的 date 做字符串比较，适用于可按字典序排序的
        ISO 日期或周期；等于下界的记录会保留。空筛选值不生效。无效
        frontmatter 标记、YAML 解析失败、非映射结果和非 Diary Markdown 会被
        跳过；其他字段不执行 schema 校验，转换失败以及 I/O、UTF-8 解码错误
        直接传播。缺失 layer 按 ``day`` 处理，显式 ``null`` 保持不变。结果按
        文件路径排序。

        Args:
            since: 包含式日期或周期下界；None 或空字符串表示不限制。
            layer: 要保留的 layer；None 或空字符串表示保留全部。

        Returns:
            ``diary`` 目录不存在时返回空列表，否则返回通过筛选的值对象。
        """

        diary_root = self.root / _DIARY_DIR
        if not diary_root.exists():
            return []
        out: list[Diary] = []
        for p in sorted(diary_root.rglob("*.md")):
            fm, body = _split_frontmatter(p.read_text(encoding="utf-8"))
            d = _diary_from_fm(fm, body)
            if d is None:
                continue
            if layer and d.layer != layer:
                continue
            if since and d.date < since:
                continue
            out.append(d)
        return out
