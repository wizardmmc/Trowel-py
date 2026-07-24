"""会话级 memory 使用质量指标。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trowel_py.memory.promotion_policy import PromotionPolicy


def memory_usage_metrics(
    root: Path | str,
    *,
    policy: "PromotionPolicy | None" = None,
    local_tz: Any | None = None,
) -> dict[str, Any]:
    """计算 identity、retrieval、effect 与 recall 指标。"""
    from trowel_py.memory.access_log import read_access_log
    from trowel_py.memory.attribution import AttributionIndex
    from trowel_py.memory.judgements import load_all_judgement_reports
    from trowel_py.memory.promotion_policy import default_policy
    from trowel_py.memory.recompute import compute_note_effects
    from trowel_py.memory.store import MemoryStore

    active_policy = policy or default_policy()
    root_path = Path(root)
    index = AttributionIndex.from_root(root_path)
    effects = compute_note_effects(root_path, local_tz=local_tz)

    resolved = [
        (record, index.resolve(record.trowel_session_id, record.cc_session_id))
        for record in read_access_log(root_path)
    ]
    records_total = len(resolved)
    attributed = sum(1 for _record, attribution in resolved if attribution.attributed)
    unattributed = records_total - attributed
    identity_coverage = round(attributed / records_total, 4) if records_total else None
    identity_quality = active_policy.identity_quality(
        identity_coverage,
        records_total,
    )

    user_records = [record for record, attribution in resolved if attribution.is_user]
    reads = sum(1 for record in user_records if record.action == "read")
    # search candidate 才是 hit；没有 memory_id 的汇总记录只代表一次调用。
    search_hits = sum(
        1 for record in user_records if record.action == "search" and record.memory_id
    )
    search_calls = sum(
        1
        for record in user_records
        if record.action == "search" and not record.memory_id
    )
    read_sessions = len(
        {
            cc_session_id
            for effect in effects.values()
            for cc_session_id in effect.read_sessions
        }
    )
    read_rate = round(reads / search_hits, 4) if search_hits else None
    retrieval_quality = active_policy.identity_quality(
        identity_coverage,
        search_hits,
    )

    helpful_sessions = sum(effect.helpful_refs for effect in effects.values())
    harmful_sessions = sum(effect.harmful_refs for effect in effects.values())
    unused_sessions = sum(effect.unused_refs for effect in effects.values())

    reports = load_all_judgement_reports(root_path)
    id_to_stem = {
        note.memory_id: stem
        for stem, note in MemoryStore(root_path).load_notes_with_id()
        if note.memory_id
    }
    judged_user_sessions: set[str] = set()
    retrieval_miss = 0
    awareness_miss = 0
    for report in reports:
        cc_session_id = report.cc_session_id
        if not cc_session_id or not index.resolve("", cc_session_id).is_user:
            continue
        judged_user_sessions.add(cc_session_id)
        for miss in report.recall_miss:
            if id_to_stem.get(miss.memory_id) is None:
                continue
            if miss.attribution == "retrieval_miss":
                retrieval_miss += 1
            elif miss.attribution == "awareness_miss":
                awareness_miss += 1

    effect_denominator = helpful_sessions + harmful_sessions + unused_sessions
    hit_quality = (
        round(helpful_sessions / effect_denominator, 4) if effect_denominator else None
    )
    judged_user_segments = len(judged_user_sessions)
    # 无 access 的 judgement 仍是合格用户证据，覆盖率分母必须取两者并集。
    access_user_sessions = {
        attribution.cc_session_id
        for _record, attribution in resolved
        if attribution.is_user
    }
    eligible_user_segments = len(access_user_sessions | judged_user_sessions)
    judgement_coverage = (
        round(judged_user_segments / eligible_user_segments, 4)
        if eligible_user_segments
        else None
    )
    effect_quality = active_policy.judgement_quality(
        judgement_coverage,
        judged_user_segments,
    )

    recall_miss_total = retrieval_miss + awareness_miss
    recall_miss_rate = (
        round(recall_miss_total / judged_user_segments, 4)
        if judged_user_segments
        else None
    )
    recall_quality = active_policy.judgement_quality(
        judgement_coverage,
        judged_user_segments,
    )

    return {
        "policy": active_policy.to_dict(),
        "identity": {
            "records_total": records_total,
            "attributed": attributed,
            "unattributed": unattributed,
            "coverage": identity_coverage,
            "quality": identity_quality,
        },
        "retrieval": {
            "search_calls": search_calls,
            "search_hits": search_hits,
            "reads": reads,
            "read_sessions": read_sessions,
            "read_rate": read_rate,
            "read_rate_numerator": reads,
            "read_rate_denominator": search_hits,
            "quality": retrieval_quality,
        },
        "effect": {
            "judged_user_segments": judged_user_segments,
            "eligible_user_segments": eligible_user_segments,
            "judgement_coverage": judgement_coverage,
            "helpful_sessions": helpful_sessions,
            "harmful_sessions": harmful_sessions,
            "unused_sessions": unused_sessions,
            "hit_quality": hit_quality,
            "hit_quality_numerator": helpful_sessions,
            "hit_quality_denominator": effect_denominator,
            "quality": effect_quality,
        },
        "recall": {
            "retrieval_miss": retrieval_miss,
            "awareness_miss": awareness_miss,
            "recall_miss_rate": recall_miss_rate,
            "recall_miss_rate_numerator": recall_miss_total,
            "recall_miss_rate_denominator": judged_user_segments,
            "quality": recall_quality,
        },
        # 尚无客观会话失败真值，不能用 recall 软指标替代。
        "known_issue_repeat_rate": None,
    }
