"""任务切换后首个 runtime 动作与恢复耗时观测。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from trowel_py.model_os.types import EventEnvelope, EventKind, Provenance


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def record_switch_started(
    store,
    *,
    decision_id: str,
    task_id: str,
    episode_id: str,
    claimed_at: str | None = None,
) -> None:
    identity = decision_id.rsplit(".", 1)[-1]
    event_id = f"event.attention.recovery.started.{identity}"
    if any(event.event_id == event_id for _, event in store.list_events()):
        return
    events = tuple(store.list_events())
    previous = next(
        (
            event.task_id
            for _, event in reversed(events)
            if event.kind == EventKind.FOREGROUND_RELEASED
        ),
        None,
    )
    claimed = claimed_at or next(
        (
            event.occurred_at
            for _, event in reversed(events)
            if event.kind == EventKind.FOREGROUND_CLAIMED and event.task_id == task_id
        ),
        _iso(_now()),
    )
    store.append_event(
        EventEnvelope(
            event_id=event_id,
            kind=EventKind.SWITCH_RECOVERY_STARTED,
            occurred_at=claimed,
            source="attention_scheduler",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="attention-v0",
            payload={
                "schedule_decision_id": decision_id,
                "from_task_id": previous,
                "to_task_id": task_id,
                "foreground_claimed_at": claimed,
            },
            task_id=task_id,
            episode_id=episode_id,
            cause_id=decision_id,
        )
    )


class SwitchRecoveryObserver:
    def __init__(self, store, *, now=_now) -> None:
        self._store = store
        self._now = now

    def observe_task_event(self, task_id: str, event) -> None:
        events = tuple(self._store.list_events())
        observed_causes = {
            item.cause_id
            for _, item in events
            if item.kind == EventKind.SWITCH_RECOVERY_OBSERVED
        }
        started = next(
            (
                item
                for _, item in reversed(events)
                if item.kind == EventKind.SWITCH_RECOVERY_STARTED
                and item.task_id == task_id
                and item.cause_id not in observed_causes
            ),
            None,
        )
        if started is None:
            return
        event_type = str(event.get("type", ""))
        terminal = event_type in {"finished", "interrupted", "error", "session_exited"}
        if event_type != "tool_call" and not terminal:
            return

        payload = event.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        tool_name = None
        tool_hash = None
        latency = None
        unavailable = None
        repeated: list[str] = []
        if event_type == "tool_call":
            tool_name = str(
                payload.get("tool_name") or payload.get("name") or "unknown"
            )
            tool_input = payload.get("input")
            if not isinstance(tool_input, dict):
                tool_input = {}
            raw = json.dumps(
                tool_input,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            tool_hash = f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"
            claimed = datetime.fromisoformat(
                str(started.payload["foreground_claimed_at"]).replace("Z", "+00:00")
            )
            latency = max(0, int((self._now() - claimed).total_seconds() * 1000))
            for _, prior in events:
                if (
                    prior.kind == EventKind.SWITCH_RECOVERY_OBSERVED
                    and prior.task_id == task_id
                    and prior.payload.get("tool_name") == tool_name
                    and prior.payload.get("tool_target_hash") == tool_hash
                ):
                    repeated.append(
                        f"tool.{tool_name}.{tool_hash.split(':', 1)[1][:16]}"
                    )
                    break
        else:
            unavailable = "no_tool_action"

        identity = str(started.cause_id).rsplit(".", 1)[-1]
        self._store.append_event(
            EventEnvelope(
                event_id=f"event.attention.recovery.observed.{identity}",
                kind=EventKind.SWITCH_RECOVERY_OBSERVED,
                occurred_at=_iso(self._now()),
                source="runtime_observer",
                provenance=Provenance.MACHINE_OBSERVATION,
                policy_version="attention-v0",
                payload={
                    "schedule_decision_id": started.cause_id,
                    "from_task_id": started.payload.get("from_task_id"),
                    "to_task_id": task_id,
                    "foreground_claimed_at": started.payload["foreground_claimed_at"],
                    "first_action_kind": "tool_call" if tool_name else None,
                    "tool_name": tool_name,
                    "tool_target_hash": tool_hash,
                    "restore_latency_ms": latency,
                    "repeated_tool_candidates": repeated,
                    "unavailable_reason": unavailable,
                },
                task_id=task_id,
                episode_id=started.episode_id,
                cause_id=started.cause_id,
            )
        )
