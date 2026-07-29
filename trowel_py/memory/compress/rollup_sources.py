"""提供 Weekly、Monthly 的上游来源扫描、周期归属判断和内容哈希。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from trowel_py.memory.store import _split_frontmatter


@dataclass(frozen=True)
class PeriodSource:
    """保存一份带标识的压缩输入。

    Attributes:
        period: Daily 的 ISO 日期、Weekly 的 ISO 周标识，或 Monthly 分批汇总
            使用的内部批次标识。
        body: 去除 frontmatter 后的 Diary 正文，或 Monthly 分批生成的中间摘要。
        frontmatter: Diary 中解析出的元数据；中间摘要使用空字典。
    """

    period: str
    body: str
    frontmatter: dict[str, Any]


def parse_iso_week(value: str) -> tuple[int, int]:
    """把 ``YYYY-Www`` 格式的 ISO 周标识解析为年份和周序号。

    Args:
        value: 使用四位年份和两位周序号的 ISO 周标识。

    Returns:
        ISO 年份和周序号。

    Raises:
        ValueError: 标识格式错误、年份超出支持范围，或该 ISO 年不存在对应周序号。
    """
    match = re.fullmatch(r"(\d{4})-W(\d{2})", value)
    if not match:
        raise ValueError(f"bad ISO week string: {value!r}")
    year, week = int(match.group(1)), int(match.group(2))
    date.fromisocalendar(year, week, 1)
    return year, week


def in_iso_week(date_str: str, iso_year: int, iso_week: int) -> bool:
    """判断 ISO 日期是否属于指定的 ISO 年份和周。

    Args:
        date_str: 要判断的 ISO 日期；无法解析时返回 False。
        iso_year: 目标 ISO 年份。
        iso_week: 目标 ISO 周序号。

    Returns:
        日期属于目标 ISO 周时为 True，否则为 False。
    """
    try:
        parsed = date.fromisoformat(date_str)
    except ValueError:
        return False
    year, week, _weekday = parsed.isocalendar()
    return year == iso_year and week == iso_week


def week_in_month(iso_week: str, month: str) -> bool:
    """判断 ISO 周的周一是否落在目标月份。

    Args:
        iso_week: ``YYYY-Www`` 格式的 ISO 周标识。
        month: ``YYYY-MM`` 格式的目标月份。

    Returns:
        周一落在目标月份时为 True；任一标识无效时为 False。
    """
    try:
        year, week = parse_iso_week(iso_week)
        return date.fromisocalendar(year, week, 1).strftime("%Y-%m") == month
    except (TypeError, ValueError):
        return False


def diary_path(root: Path, layer: str, period: str) -> Path:
    """返回 Daily、Weekly 或 Monthly 的文件路径。

    Args:
        root: memory 数据目录。
        layer: Diary 层级，允许 ``"day"``、``"week"`` 或 ``"month"``。
        period: 要写入文件名的日期、ISO 周或月份标识。

    Returns:
        对应 ``diary`` 子目录中的 Markdown 文件路径。

    Raises:
        KeyError: ``layer`` 不是受支持的 Diary 层级。
    """
    directory = {"day": "daily", "week": "weekly", "month": "monthly"}[layer]
    return root / "diary" / directory / f"{period}.md"


def _load_source(root: Path, layer: str, period: str) -> PeriodSource | None:
    """读取一份可供周或月压缩使用的 Diary。

    Args:
        root: memory 数据目录。
        layer: 要读取的 Diary 层级。
        period: 要读取的日期、ISO 周或月份标识。

    Returns:
        文件存在，且包含 ``type: diary`` 的非空映射型 frontmatter 时返回来源；
        文件缺失、frontmatter 无法解析为非空映射或 ``type`` 不匹配时返回 None。
    """
    path = diary_path(root, layer, period)
    if not path.is_file():
        return None
    frontmatter, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    if not frontmatter or frontmatter.get("type") != "diary":
        return None
    return PeriodSource(period, body, frontmatter)


def weekly_sources(root: Path | str, iso_week: str) -> list[PeriodSource]:
    """按文件名顺序读取目标 ISO 周内的 Daily 来源。

    只扫描 ``diary/daily`` 中的 Markdown 文件；日期文件名无效或 frontmatter
    不是 Diary 的文件会被跳过。

    Args:
        root: memory 数据目录。
        iso_week: ``YYYY-Www`` 格式的目标 ISO 周。

    Returns:
        文件名可解析为目标周日期的 Daily 来源，按文件名升序排列；规范的
        ``YYYY-MM-DD`` 文件名因此也是日期升序。目录不存在时为空。

    Raises:
        ValueError: ``iso_week`` 格式错误或对应周不存在。
    """
    year, week = parse_iso_week(iso_week)
    root_path = Path(root)
    daily_dir = root_path / "diary" / "daily"
    if not daily_dir.is_dir():
        return []
    sources: list[PeriodSource] = []
    for path in sorted(daily_dir.glob("*.md")):
        if not in_iso_week(path.stem, year, week):
            continue
        source = _load_source(root_path, "day", path.stem)
        if source is not None:
            sources.append(source)
    return sources


def monthly_sources(root: Path | str, month: str) -> list[PeriodSource]:
    """按周顺序读取周一落在目标月份的 Weekly 来源。

    只扫描 ``diary/weekly`` 中的 Markdown 文件；ISO 周文件名无效或 frontmatter
    不是 Diary 的文件会被跳过。

    Args:
        root: memory 数据目录。
        month: ``YYYY-MM`` 格式的目标月份。

    Returns:
        周一落在目标月份的 Weekly 来源，按 ISO 周升序排列；目录不存在或月份
        格式无效时为空。
    """
    root_path = Path(root)
    weekly_dir = root_path / "diary" / "weekly"
    if not weekly_dir.is_dir():
        return []
    sources: list[PeriodSource] = []
    for path in sorted(weekly_dir.glob("*.md")):
        if not week_in_month(path.stem, month):
            continue
        source = _load_source(root_path, "week", path.stem)
        if source is not None:
            sources.append(source)
    return sources


def source_hash(sources: list[PeriodSource]) -> str:
    """计算用于判断 Weekly 或 Monthly 是否过期的来源哈希。

    哈希纳入每份来源的周期、正文、上游哈希、生成状态、生成版本和结构化条目；
    来源列表及每份来源的 ``items`` 列表顺序都会影响结果，生成时间等其他元数据
    不参与计算。

    Args:
        sources: 已按目标周期排序的上游 Diary 来源。

    Returns:
        稳定 JSON 内容的 SHA-256 哈希前 16 位十六进制字符。
    """
    payload = [
        {
            "period": source.period,
            "body": source.body,
            "source_hash": source.frontmatter.get("source_hash"),
            "generation_status": source.frontmatter.get("generation_status"),
            "generation_version": source.frontmatter.get("generation_version"),
            "items": source.frontmatter.get("items"),
        }
        for source in sources
    ]
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
