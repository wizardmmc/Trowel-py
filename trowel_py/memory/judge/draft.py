"""把 judge agent 的 JSON 草稿宽松转换为判效报告。"""

from __future__ import annotations

import json
from typing import cast

from trowel_py.memory.judgements import (
    VALID_ATTRIBUTIONS,
    VALID_OUTCOMES,
    Attribution,
    HitJudgement,
    JudgementReport,
    MissJudgement,
    Outcome,
)


def _coerce_bool(value: object) -> bool:
    """按 judge 兼容规则转换布尔值。

    字符串去除首尾空白并转为小写后，只有 ``"false"``、``"0"``、``"no"``
    和空字符串为假；其他字符串为真。非字符串沿用 Python 真值规则。
    """
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "")
    return bool(value)


def _parse_draft(
    text: str,
    *,
    cc_session_id: str,
    segment_id: str = "",
    activity_dates: tuple[str, ...] = (),
) -> JudgementReport:
    """解析判效 JSON，并规范化 Hit 与 Recall miss。

    顶层必须是对象。``hits`` 和 ``recall_miss`` 的假值按空序列处理，其他
    值直接迭代，其中非对象项会被跳过。Hit 的未知 outcome 会改为
    ``"unknown"``，Recall miss 的未知 attribution 则整项丢弃。
    ``memory_id``、``reason``、``evidence`` 和 ``summary`` 的假值归一为空
    字符串，其余值使用 ``str()``；Memory ID 的真实性不在此处检查，由 judge
    facade 在保存前另行过滤。

    Args:
        text: ``judgement-draft.json`` 的完整文本。
        cc_session_id: 写入报告的被判效 CC 会话 ID。
        segment_id: 写入报告的可选来源片段 ID。
        activity_dates: 已从真实来源事件、完成时间或登记时间确认的活动日期。

    Returns:
        分别保持 ``hits`` 和 ``recall_miss`` 中保留项原顺序的判效报告。

    Raises:
        JudgeError: 文本不是合法 JSON，或顶层 JSON 不是对象。
        TypeError: ``hits`` 或 ``recall_miss`` 为真值但不可迭代，或 outcome、
            attribution 不可哈希。
    """
    from trowel_py.memory.judge import JudgeError

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise JudgeError(f"judgement-draft.json is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise JudgeError("judgement-draft.json is not a JSON object")

    hits: list[HitJudgement] = []
    for item in data.get("hits", []) or []:
        if not isinstance(item, dict):
            continue
        outcome = item.get("outcome")
        if outcome not in VALID_OUTCOMES:
            outcome = "unknown"
        hits.append(
            HitJudgement(
                memory_id=str(item.get("memory_id") or ""),
                used=_coerce_bool(item.get("used")),
                outcome=cast(Outcome, outcome),
                reason=str(item.get("reason") or ""),
                evidence=str(item.get("evidence") or ""),
            )
        )

    misses: list[MissJudgement] = []
    for item in data.get("recall_miss", []) or []:
        if not isinstance(item, dict):
            continue
        attribution = item.get("attribution")
        if attribution not in VALID_ATTRIBUTIONS:
            continue
        misses.append(
            MissJudgement(
                memory_id=str(item.get("memory_id") or ""),
                attribution=cast(Attribution, attribution),
                reason=str(item.get("reason") or ""),
                evidence=str(item.get("evidence") or ""),
            )
        )

    return JudgementReport(
        cc_session_id=cc_session_id,
        hits=tuple(hits),
        recall_miss=tuple(misses),
        summary=str(data.get("summary") or ""),
        segment_id=segment_id,
        activity_dates=activity_dates,
    )
