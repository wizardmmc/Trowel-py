"""唤醒域的冻结值对象。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any


class WakeConditionKind(str, Enum):
    TIME = "time"
    USER_INPUT = "user_input"
    HOST_EVENT = "host_event"
    OBSERVED_STATE = "observed_state"
    MANUAL = "manual"


class WakeCatchupPolicy(str, Enum):
    MERGE_ONCE = "merge_once"
    NO_CATCHUP = "no_catchup"


class WakeDisposition(str, Enum):
    READY = "ready"
    SUSPENDED_READY = "suspended_ready"
    UNKNOWN_REQUIRES_USER_RESTART = "unknown_requires_user_restart"


@dataclass(frozen=True)
class WakeCondition:
    condition_id: str
    task_id: str
    kind: WakeConditionKind
    target_ref: str
    match_params: dict[str, Any]
    due_at: str | None
    catchup_policy: WakeCatchupPolicy
    registered_at: str
    episode_id: str | None = None


@dataclass(frozen=True)
class WakeObservation:
    observation_id: str
    kind: WakeConditionKind
    target_ref: str
    observed_at: str
    source: str
    details: dict[str, Any]

    def __post_init__(self) -> None:
        required = (
            self.observation_id,
            self.target_ref,
            self.observed_at,
            self.source,
        )
        if any(not isinstance(value, str) or not value for value in required):
            raise ValueError("wake observation identity fields must be non-empty")

    @property
    def fingerprint(self) -> str:
        raw = json.dumps(
            {
                "observation_id": self.observation_id,
                "kind": self.kind.value,
                "target_ref": self.target_ref,
                "observed_at": self.observed_at,
                "source": self.source,
                "details": self.details,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WakeEvent:
    wake_id: str
    condition_id: str
    observation_id: str
    task_id: str
    episode_id: str | None
    dedupe_key: str
    observed_at: str
    source: str
    catchup_policy: WakeCatchupPolicy
    disposition: WakeDisposition
    observation_fingerprint: str
