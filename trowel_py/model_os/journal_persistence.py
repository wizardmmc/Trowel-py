"""Event/Decision 账本跨组件共用的写入编码。"""

from __future__ import annotations

import json
from typing import Any

from trowel_py.model_os.journal import decision_fingerprint, validate_decision
from trowel_py.model_os.redaction import redact_payload
from trowel_py.model_os.store_journal_codec import decision_params as _decision_params
from trowel_py.model_os.types import DecisionRecord

DECISION_INSERT_SQL = (
    "INSERT INTO decisions (decision_id, kind, disposition, decided_at, work_item_id, "
    "task_id, episode_id, cause_id, correlation_id, policy_version, "
    "signals, candidates, choice, reason, budget_before, budget_after, identity_hash) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def decision_params(decision: DecisionRecord) -> tuple[Any, ...]:
    validate_decision(decision)
    return _decision_params(
        decision,
        dumps_fn=lambda value: json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        redact_fn=redact_payload,
        identity_hash=decision_fingerprint(decision, redact_fn=redact_payload),
    )
