from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from trowel_py.model_os.types import SnapshotRef
from trowel_py.model_os.waking import (
    WakeCatchupPolicy,
    WakeConditionKind,
    WakeDisposition,
    WakeEvent,
)

from .models import IncubationCandidateDraft, IncubationError


def parse_candidate_output(
    raw: str, allowed_source_refs: set[str]
) -> IncubationCandidateDraft:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IncubationError("output_schema_invalid") from exc
    if not isinstance(data, dict) or set(data) != {
        "proposal",
        "source_refs",
        "new_points",
        "verification",
        "uncertainty",
    }:
        raise IncubationError("output_schema_invalid")
    proposal = data["proposal"]
    source_refs = data["source_refs"]
    new_points = data["new_points"]
    verification = data["verification"]
    uncertainty = data["uncertainty"]
    if (
        not isinstance(proposal, str)
        or not isinstance(source_refs, list)
        or not all(isinstance(item, str) and item for item in source_refs)
        or not set(source_refs).issubset(allowed_source_refs)
        or not isinstance(new_points, list)
        or len(new_points) > 3
        or not all(isinstance(item, str) and item.strip() for item in new_points)
        or not isinstance(verification, str)
        or not isinstance(uncertainty, str)
    ):
        raise IncubationError("output_schema_invalid")
    if new_points and (
        not proposal.strip() or not verification.strip() or not uncertainty.strip()
    ):
        raise IncubationError("output_schema_invalid")
    return IncubationCandidateDraft(
        proposal.strip(),
        tuple(source_refs),
        tuple(item.strip() for item in new_points),
        verification.strip(),
        uncertainty.strip(),
    )


def encode_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("occurred_at must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def encode_snapshot_ref(ref: SnapshotRef) -> str:
    return encode_json(asdict(ref))


def decode_snapshot_ref(raw: str) -> SnapshotRef:
    return SnapshotRef(**json.loads(raw))


def encode_wake_event(wake: WakeEvent) -> str:
    return encode_json(
        {
            "wake_id": wake.wake_id,
            "condition_id": wake.condition_id,
            "observation_id": wake.observation_id,
            "task_id": wake.task_id,
            "episode_id": wake.episode_id,
            "dedupe_key": wake.dedupe_key,
            "observed_at": wake.observed_at,
            "source": wake.source,
            "catchup_policy": wake.catchup_policy.value,
            "disposition": wake.disposition.value,
            "observation_fingerprint": wake.observation_fingerprint,
        }
    )


def decode_wake_event(raw: str) -> WakeEvent:
    data = json.loads(raw)
    return WakeEvent(
        wake_id=data["wake_id"],
        condition_id=data["condition_id"],
        observation_id=data["observation_id"],
        task_id=data["task_id"],
        episode_id=data["episode_id"],
        dedupe_key=data["dedupe_key"],
        observed_at=data["observed_at"],
        source=data["source"],
        catchup_policy=WakeCatchupPolicy(data["catchup_policy"]),
        disposition=WakeDisposition(data["disposition"]),
        observation_fingerprint=data["observation_fingerprint"],
    )


def wake_condition_payload(condition) -> dict[str, Any]:
    return {
        "kind": condition.kind.value,
        "target_ref": condition.target_ref,
        "match_params": condition.match_params,
        "due_at": condition.due_at,
    }


def decode_wake_condition(raw: str, factory):
    data = json.loads(raw)
    return factory(
        kind=WakeConditionKind(data["kind"]),
        target_ref=data["target_ref"],
        match_params=data["match_params"],
        due_at=data["due_at"],
    )
