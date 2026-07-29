"""汇总 Memory 身份归因、检索、效果和 recall 使用质量指标。

四组指标使用不同粒度：身份覆盖率按访问日志事件，检索按合格用户访问事件，效果
按 Note–用户会话对，判断覆盖率按去重用户会话，recall miss 按报告条目。比例保留
四位小数；没有分母时返回 ``None``，不会用 0.0 假装已有样本。
"""

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
    """从访问日志、会话归因、Note 效果和判断报告计算使用质量。

    identity 统计全部成功解码的访问事件。只要能通过 Trowel 绑定或非空
    Claude Code 会话 ID 得到归属就计为 attributed，即使该 Claude Code 会话
    不在索引中、用途为 unknown；coverage 是 attributed / records_total。其
    可信度标签使用该覆盖率和全部事件数作为策略样本。

    retrieval 只统计归因为 ``user`` 且 Memory 资格为 ``eligible`` 的访问事件。
    action 为 ``search`` 且 ``memory_id`` 非空的每条记录算一个 search hit，为空
    的记录算一次 search call；每条 ``read`` 记录都计入 reads。read_rate 是
    reads / search_hits，可能大于 1；没有 hit 时为 None。read_sessions 则来自
    当前存在 Note 的重算效果，是所有实际读取会话 ID 的并集。retrieval 的
    quality 仍使用全体事件的 identity coverage，并以 search_hits 为样本量，
    不按 read_rate 评分。

    effect 先按 Note 和用户会话对聚合 helpful、harmful、unused，再跨 Note 求和；
    同一会话作用于多条 Note 会贡献多次。同一 Note–会话对出现多类证据时，只按
    harmful、helpful、unused 的优先顺序计入一个类别。hit_quality 的分母不含
    unknown，为 helpful / (helpful + harmful + unused)，无可判效果时为 None。

    判断报告仅接受会话索引中 ``user`` 且 ``eligible`` 的非空 Claude Code
    会话 ID。
    judged_user_segments 与 eligible_user_segments 虽沿用 segment 字段名，实际
    都是去重 Claude Code 会话数：前者来自判断报告，后者是有访问证据或判断
    报告的用户会话并集。judgement_coverage 是两者之比；没有合格会话时为
    None。

    recall 只统计 ``memory_id`` 仍对应当前 Note 的 ``retrieval_miss`` 和
    ``awareness_miss`` 条目，不在会话内或跨分段去重。recall_miss_rate 以去重
    judged user 会话数为分母，因此一个会话有多条 miss 时结果可以大于 1；没有
    judged user 会话时为 None。effect 与 recall 的 quality 都使用 judgement
    coverage 和 judged user 会话数。

    空根目录不会创建文件。已有 ``meta/sessions.db`` 时，归因索引按会话仓储的
    初始化规则打开数据库，可能补齐 schema；其他数据只读。

    Args:
        root: 访问日志、会话数据库、Note 和判断报告所在的 Memory 根目录。
        policy: 计算可信度标签并回显到报告的晋升策略；为 None 或其他假值时使用
            默认策略。
        local_tz: 重算 Note 效果时解释活动日期的时区；为 None 或其他假值时使用
            系统本地时区。

    Returns:
        包含生效策略、identity、retrieval、effect、recall 四组指标，以及固定为
        ``None`` 的 ``known_issue_repeat_rate`` 占位值的字典。比例字段同时返回
        对应分子和分母。
    """
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
    # 有 memory_id 的 search 记录是一条候选命中；空值记录只代表一次搜索调用。
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
    # 字段虽沿用 segment 命名，覆盖率实际按访问与判断证据的 Claude Code 会话并集。
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
