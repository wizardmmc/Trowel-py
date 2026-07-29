"""在 Episode 快照对象与持久化字段之间转换，并校验写入条件。

脱敏、内容哈希和数据库读写由 Store 负责。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def pending_to_payload(pending: Any) -> dict[str, Any]:
    """把 Episode 的等待请求编码成快照字段。

    Args:
        pending: 要编码的 ``PendingDescriptor``。

    Returns:
        可写入快照 ``waiting_condition`` 字段的字典。
    """

    return {
        "kind": pending.kind.value,
        "native_generation": pending.native_generation,
        "correlation_id": pending.correlation_id,
        "cause": pending.cause,
        "posed_at": pending.posed_at,
    }


def pending_from_payload(
    payload: dict[str, Any],
    *,
    pending_type: Callable[..., Any],
    waiting_subtype: Callable[[Any], Any],
) -> Any:
    """从快照字段构造 Episode 的等待请求。

    Args:
        payload: 等待请求字段。``kind``、``correlation_id`` 和 ``posed_at`` 必须
            存在；缺少 ``native_generation`` 或 ``cause`` 时分别使用 None 和空
            字符串。
        pending_type: 构造等待请求的类型。
        waiting_subtype: 把 ``kind`` 字段转换为等待类型的函数。

    Returns:
        由 ``pending_type`` 构造的等待请求。

    Raises:
        KeyError: ``payload`` 缺少必填字段。
        ValueError: ``kind`` 不是有效的等待类型。
    """

    return pending_type(
        kind=waiting_subtype(payload["kind"]),
        native_generation=payload.get("native_generation"),
        correlation_id=payload["correlation_id"],
        cause=payload.get("cause", ""),
        posed_at=payload["posed_at"],
    )


def snapshot_to_payload(
    snapshot: Any,
    *,
    encode_pending: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    """把完整 EpisodeSnapshot 转换为持久化前的字段字典。

    Args:
        snapshot: 要编码的 Episode 快照。
        encode_pending: 编码 ``waiting_condition`` 的函数；没有等待请求时不会调用。

    Returns:
        由基本 JSON 值组成的快照字段；结果尚未脱敏、序列化或计算哈希。
    """

    return {
        "work_item_goal": snapshot.work_item_goal,
        "task_constraints_ref": snapshot.task_constraints_ref,
        "current_judgment": snapshot.current_judgment,
        "completed_with_evidence": [
            list(pair) for pair in snapshot.completed_with_evidence
        ],
        "side_effects": [
            {
                "action_ref": side_effect.action_ref,
                "idempotency_key": side_effect.idempotency_key,
                "outcome": side_effect.outcome,
                "evidence_ref": side_effect.evidence_ref,
            }
            for side_effect in snapshot.side_effects
        ],
        "unknowns": list(snapshot.unknowns),
        "waiting_condition": (
            encode_pending(snapshot.waiting_condition)
            if snapshot.waiting_condition
            else None
        ),
        "next_steps": list(snapshot.next_steps),
        "artifacts": [
            {"kind": artifact.kind, "ref": artifact.ref}
            for artifact in snapshot.artifacts
        ],
        "native_transcript_ref": snapshot.native_transcript_ref,
        "source": snapshot.source.value,
        "journal_through_seq": snapshot.journal_through_seq,
        "base_snapshot_ref": (
            {
                "episode_id": snapshot.base_snapshot_ref.episode_id,
                "version": snapshot.base_snapshot_ref.version,
                "committed_event_id": (snapshot.base_snapshot_ref.committed_event_id),
                "payload_hash": snapshot.base_snapshot_ref.payload_hash,
            }
            if snapshot.base_snapshot_ref
            else None
        ),
    }


def validate_snapshot(
    snapshot: Any,
    payload_text: str,
    *,
    max_payload_bytes: int,
    error_type: type[Exception],
) -> None:
    """在持久化前校验快照大小、后续步骤和完成证据。

    ``next_steps`` 最多包含三项。完成记录的 action 和证据引用都不能为空，结果为
    ``done`` 的副作用也必须带 ``evidence_ref``。

    Args:
        snapshot: 要校验的 Episode 快照。
        payload_text: 即将持久化的快照文本，大小按 UTF-8 字节数计算。
        max_payload_bytes: 允许的最大字节数；大小等于该值时仍可通过。
        error_type: 任一规则不满足时使用的异常类型，必须能接收错误消息。

    Raises:
        error_type: 快照文本超过上限，或快照内容不满足上述规则。
    """

    if len(payload_text.encode("utf-8")) > max_payload_bytes:
        raise error_type(
            f"snapshot payload exceeds {max_payload_bytes} bytes "
            f"(got {len(payload_text.encode('utf-8'))}); reduce content or "
            f"reference instead of copying"
        )
    if len(snapshot.next_steps) > 3:
        raise error_type(
            f"next_steps must have at most 3 items (got {len(snapshot.next_steps)})"
        )
    for action_ref, evidence_ref in snapshot.completed_with_evidence:
        if not action_ref or not evidence_ref:
            raise error_type(
                "completed_with_evidence entries must be non-empty "
                "(action_ref, evidence_ref)"
            )
    for side_effect in snapshot.side_effects:
        if side_effect.outcome == "done" and not side_effect.evidence_ref:
            raise error_type(
                f"side effect {side_effect.action_ref!r} marked done "
                f"without an evidence_ref; record it "
                f"unknown_requires_reconcile instead"
            )


def snapshot_from_payload(
    payload: dict[str, Any],
    *,
    decode_pending: Callable[[dict[str, Any]], Any],
    snapshot_type: Callable[..., Any],
    side_effect_type: Callable[..., Any],
    artifact_type: Callable[..., Any],
    snapshot_ref_type: Callable[..., Any],
    snapshot_source: Callable[[Any], Any],
) -> Any:
    """从持久化字段构造 EpisodeSnapshot 及其嵌套对象。

    缺少顶层字段时使用历史缺省值，未知顶层字段会被忽略。空的
    ``waiting_condition`` 和 ``base_snapshot_ref`` 视为 None；
    ``journal_through_seq`` 和基线快照的 ``version`` 会转换为整数。

    Args:
        payload: 从持久化文本解析出的快照字段。
        decode_pending: 把非空 ``waiting_condition`` 字段转换为等待请求的函数。
        snapshot_type: 构造 Episode 快照的类型。
        side_effect_type: 构造每项副作用记录的类型。
        artifact_type: 构造每项产物引用的类型。
        snapshot_ref_type: 构造基线快照引用的类型。
        snapshot_source: 把 ``source`` 字段转换为快照来源的函数。

    Returns:
        由 ``snapshot_type`` 构造的 Episode 快照。

    Raises:
        KeyError: 非空嵌套对象缺少必填字段。
        TypeError: 字段无法按预期结构迭代、索引或转换。
        ValueError: 等待类型、快照来源或整数字段的值无效。
    """

    waiting = payload.get("waiting_condition")
    base = payload.get("base_snapshot_ref")
    return snapshot_type(
        work_item_goal=payload.get("work_item_goal", ""),
        task_constraints_ref=payload.get("task_constraints_ref"),
        current_judgment=payload.get("current_judgment", "unknown"),
        completed_with_evidence=tuple(
            tuple(pair) for pair in payload.get("completed_with_evidence", [])
        ),
        side_effects=tuple(
            side_effect_type(
                action_ref=side_effect["action_ref"],
                idempotency_key=side_effect["idempotency_key"],
                outcome=side_effect["outcome"],
                evidence_ref=side_effect.get("evidence_ref"),
            )
            for side_effect in payload.get("side_effects", [])
        ),
        unknowns=tuple(payload.get("unknowns", [])),
        waiting_condition=decode_pending(waiting) if waiting else None,
        next_steps=tuple(payload.get("next_steps", [])),
        artifacts=tuple(
            artifact_type(kind=artifact["kind"], ref=artifact["ref"])
            for artifact in payload.get("artifacts", [])
        ),
        native_transcript_ref=payload.get("native_transcript_ref"),
        source=snapshot_source(payload.get("source", "cooperative")),
        journal_through_seq=int(payload.get("journal_through_seq", 0)),
        base_snapshot_ref=(
            snapshot_ref_type(
                episode_id=base["episode_id"],
                version=int(base["version"]),
                committed_event_id=base["committed_event_id"],
                payload_hash=base["payload_hash"],
            )
            if base
            else None
        ),
    )
