"""EpisodeSnapshot codec；脱敏、hash 与持久化仍由 Store 负责。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def pending_to_payload(pending: Any) -> dict[str, Any]:
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
