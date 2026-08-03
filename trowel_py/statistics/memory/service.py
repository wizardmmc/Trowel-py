"""把 Memory 公开快照转换为 Statistics API 的稳定 DTO。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol

from trowel_py.statistics.memory.schemas import (
    MemoryAssetsData,
    MemoryAttributionData,
    MemoryEffectData,
    MemoryRatioData,
    MemoryRecallData,
    MemoryRetrievalData,
    MemorySourceData,
    MemoryStatisticsData,
    Quality,
)
from trowel_py.statistics.schemas import SourceFreshness
from trowel_py.statistics.window import StatisticsWindow


class MemoryStatisticsReader(Protocol):
    """约束 Statistics service 所需的 Memory 只读来源。"""

    def read(self, window: StatisticsWindow) -> dict[str, Any]:
        """返回不含正文和身份标识的 Memory 快照。"""
        ...


def build_memory_statistics(
    reader: MemoryStatisticsReader,
    window: StatisticsWindow,
    *,
    now: datetime | None = None,
) -> MemoryStatisticsData:
    """构造 Memory 页 read model，并保留每个指标自己的分母。

    Args:
        reader: 通过 Memory 公开门面读取事实的来源。
        window: 已解析的半开查询时间窗。
        now: 可选生成时间；测试省略时使用当前带时区时间。

    Returns:
        不含 Note 正文、查询正文和会话标识的公开 DTO。
    """
    snapshot = reader.read(window)
    usage = snapshot["usage"]
    assets = snapshot["assets"]
    sources = {
        name: MemorySourceData.model_validate(value)
        for name, value in snapshot["sources"].items()
    }
    freshness = {
        name: SourceFreshness(
            updated_at=source.updated_at,
            status=_freshness_status(name, source, snapshot["sources"][name]),
        )
        for name, source in sources.items()
    }
    identity = usage["identity"]
    retrieval = usage["retrieval"]
    effect = usage["effect"]
    recall = usage["recall"]
    identity_quality = _quality(identity["quality"])
    retrieval_quality = _quality(retrieval["quality"])
    effect_quality = _quality(effect["quality"])
    recall_quality = _quality(recall["quality"])
    assets_quality = _worst_quality(
        sources["notes"].quality,
        sources["dictionary"].quality,
    )
    return MemoryStatisticsData(
        generated_at=now or datetime.now().astimezone(),
        window_start=window.start,
        window_end=window.end,
        timezone=window.timezone,
        sample_size=identity["records_total"],
        quality=_worst_quality(*(source.quality for source in sources.values())),
        freshness=freshness,
        sources=sources,
        attribution=MemoryAttributionData(
            attributed=identity["attributed"],
            unattributed=identity["unattributed"],
            coverage=_ratio(
                identity["attributed"],
                identity["records_total"],
                identity["coverage"],
                identity_quality,
            ),
            quality=identity_quality,
        ),
        retrieval=MemoryRetrievalData(
            search_calls=retrieval["search_calls"],
            nonempty_search_calls=retrieval["nonempty_search_calls"],
            empty_search_calls=retrieval["empty_search_calls"],
            search_hits=retrieval["search_hits"],
            reads=retrieval["reads"],
            read_sessions=retrieval["read_sessions"],
            read_rate=_ratio(
                retrieval["read_rate_numerator"],
                retrieval["read_rate_denominator"],
                retrieval["read_rate"],
                retrieval_quality,
            ),
            quality=retrieval_quality,
        ),
        effect=MemoryEffectData(
            helpful=effect["helpful_sessions"],
            harmful=effect["harmful_sessions"],
            unused=effect["unused_sessions"],
            unknown=effect["unknown_sessions"],
            judged_user_sessions=effect["judged_user_segments"],
            eligible_user_sessions=effect["eligible_user_segments"],
            judgement_coverage=_ratio(
                effect["judged_user_segments"],
                effect["eligible_user_segments"],
                effect["judgement_coverage"],
                effect_quality,
            ),
            helpful_rate=_ratio(
                effect["hit_quality_numerator"],
                effect["hit_quality_denominator"],
                effect["hit_quality"],
                effect_quality,
            ),
            quality=effect_quality,
        ),
        recall=MemoryRecallData(
            retrieval_miss=recall["retrieval_miss"],
            awareness_miss=recall["awareness_miss"],
            judged_user_sessions=recall["recall_miss_rate_denominator"],
            miss_rate=_ratio(
                recall["recall_miss_rate_numerator"],
                recall["recall_miss_rate_denominator"],
                recall["recall_miss_rate"],
                recall_quality,
            ),
            quality=recall_quality,
        ),
        assets=MemoryAssetsData(
            as_of=assets["as_of"],
            active_notes=assets["active_notes"],
            raw_reads=assets["raw_reads"],
            raw_harmful_outcomes=assets["raw_harmful_outcomes"],
            contradicted_or_superseded=assets["contradicted_or_superseded"],
            harmful_high_notes=assets["harmful_high_notes"],
            dictionary_status=assets["dictionary_status"],
            dictionary_updated_at=assets["dictionary_updated_at"],
            quality=assets_quality,
        ),
    )


def _ratio(
    numerator: int,
    denominator: int,
    ratio: float | None,
    quality: Quality,
) -> MemoryRatioData:
    """构造保留原始计数的比例 DTO。"""
    return MemoryRatioData(
        numerator=numerator,
        denominator=denominator,
        ratio=ratio,
        quality=quality,
    )


def _quality(value: str) -> Quality:
    """把 Memory 的 insufficient 映射为 Statistics unavailable。"""
    if value == "reliable":
        return "reliable"
    if value == "partial":
        return "partial"
    return "unavailable"


def _worst_quality(*values: Quality) -> Quality:
    """返回一组来源中最弱的数据质量。"""
    rank: dict[Quality, int] = {
        "reliable": 0,
        "partial": 1,
        "unavailable": 2,
    }
    return max(values, key=rank.__getitem__) if values else "unavailable"


def _freshness_status(
    name: str,
    source: MemorySourceData,
    raw_source: dict[str, Any],
) -> Literal["fresh", "stale", "unavailable"]:
    """把 Dictionary stale 和其他来源可用性映射到共用新鲜度状态。"""
    if source.quality == "unavailable":
        return "unavailable"
    if name == "dictionary" and raw_source.get("status") == "stale":
        return "stale"
    if source.updated_at is None:
        return "unavailable"
    return "fresh"
