from __future__ import annotations

import json
from pathlib import Path

import pytest

from trowel_py.codex_host.pending_requests import (
    PendingRequestConflictError,
    PendingRequestDecisionError,
    PendingRequestKind,
    PendingRequestOwnershipError,
    PendingRequestRegistry,
    PendingRequestStatus,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _recorded_command() -> dict:
    return json.loads(
        (FIXTURES / "server-request-approval.jsonl").read_text(encoding="utf-8")
    )


def _create_command(registry: PendingRequestRegistry):
    message = _recorded_command()
    return registry.create(
        native_request_id=message["id"],
        generation=7,
        session_id="session-a",
        kind=PendingRequestKind.COMMAND_APPROVAL,
        params=message["params"],
    )


async def test_resolve_preserves_native_decision_and_is_single_use() -> None:
    registry = PendingRequestRegistry()
    pending = _create_command(registry)

    resolved = registry.resolve("session-a", pending.request_id, "accept")

    assert resolved.status is PendingRequestStatus.ANSWERED
    assert resolved.decision == "accept"
    assert await pending.response == {"decision": "accept"}
    with pytest.raises(PendingRequestConflictError):
        registry.resolve("session-a", pending.request_id, "cancel")


async def test_structured_execpolicy_choice_round_trips_recorded_object() -> None:
    registry = PendingRequestRegistry()
    pending = _create_command(registry)

    registry.resolve("session-a", pending.request_id, "acceptWithExecpolicyAmendment")

    response = await pending.response
    assert response["decision"] == pending.available_decisions[1]
    assert (
        "execpolicy_amendment" in response["decision"]["acceptWithExecpolicyAmendment"]
    )


async def test_wrong_owner_and_unadvertised_decision_are_rejected() -> None:
    registry = PendingRequestRegistry()
    pending = _create_command(registry)

    with pytest.raises(PendingRequestOwnershipError):
        registry.resolve("session-b", pending.request_id, "accept")
    with pytest.raises(PendingRequestDecisionError):
        registry.resolve("session-a", pending.request_id, "decline")
    assert pending.status is PendingRequestStatus.PENDING


async def test_expire_safely_declines_and_host_close_invalidates_generation() -> None:
    registry = PendingRequestRegistry()
    expired = _create_command(registry)
    registry.expire(expired.request_id)
    assert expired.status is PendingRequestStatus.EXPIRED
    assert await expired.response == {"decision": "decline"}

    pending = registry.create(
        native_request_id=1,
        generation=8,
        session_id="session-a",
        kind=PendingRequestKind.COMMAND_APPROVAL,
        params=_recorded_command()["params"],
    )
    closed = registry.close_generation(8)
    assert closed == (pending,)
    assert pending.status is PendingRequestStatus.HOST_CLOSED
    assert pending.response.cancelled()
    with pytest.raises(PendingRequestConflictError):
        registry.resolve("session-a", pending.request_id, "accept")


async def test_public_id_contains_connection_generation() -> None:
    registry = PendingRequestRegistry()
    first = _create_command(registry)
    second = registry.create(
        native_request_id=0,
        generation=8,
        session_id="session-a",
        kind=PendingRequestKind.COMMAND_APPROVAL,
        params=_recorded_command()["params"],
    )
    assert first.request_id != second.request_id
    assert first.request_id.startswith("7-")
    assert second.request_id.startswith("8-")
    payload = first.to_payload()
    assert payload["session_id"] == "session-a"
    assert payload["thread_id"] == _recorded_command()["params"]["threadId"]
    assert payload["turn_id"] == _recorded_command()["params"]["turnId"]
