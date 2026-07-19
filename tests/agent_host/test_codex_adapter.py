"""Tests for the Codex → AgentEvent v1 adapter (slice-074).

The adapter maps a :class:`~trowel_py.codex_host.events.CodexEvent` (Codex's
own translation, reflecting the app-server protocol) onto the unified envelope
with **TrowelEvent-aligned type names** (people-confirmed 2026-07-19: unify to
the CC contract, not the spec's aspirational v1 base list).

Field shapes come from the real Codex pipeline — translator.py + session.py —
constructed here via the real dataclass + ``immutable_payload`` so nothing is
synthesised from prose.
"""

from __future__ import annotations

import pytest

from trowel_py.agent_host.codex_adapter import CodexEventAdapter
from trowel_py.codex_host.events import (
    CodexEvent,
    CodexEventType,
    immutable_payload,
)
from trowel_py.schemas.agent_host import AGENT_EVENT_SCHEMA, AgentEvent


def _codex(
    type_: CodexEventType,
    *,
    seq: int,
    thread_id: str | None = "thr-1",
    turn_id: str | None = "turn-1",
    item_id: str | None = None,
    payload: dict | None = None,
    session_id: str = "codex-sess",
) -> CodexEvent:
    """Build a CodexEvent the way session.py / translator.py do."""

    return CodexEvent(
        session_id=session_id,
        seq=seq,
        type=type_,
        thread_id=thread_id,
        turn_id=turn_id,
        item_id=item_id,
        payload=immutable_payload(**(payload or {})),
    )


@pytest.fixture()
def adapter() -> CodexEventAdapter:
    return CodexEventAdapter(session_id="codex-sess")


class TestTypeAlignment:
    """Codex type names map onto the TrowelEvent vocabulary."""

    def test_session_started_maps_and_carries_model_cwd(self, adapter) -> None:
        """session_started → session_started with model/cwd/cc_session_id/tools."""

        ev = adapter.wrap(
            _codex(
                CodexEventType.SESSION_STARTED,
                seq=1,
                payload={
                    "model": "gpt-5.6-sol",
                    "model_provider": "openai",
                    "cwd": "/repo",
                    "sandbox": {"mode": "workspace-write"},
                    "approval_policy": {"policy": "on-request"},
                    "permission_profile": ":workspace-write",
                    "effective_sandbox": "workspace-write",
                    "effective_approval": "on-request",
                    "network_access": False,
                },
            )
        )
        assert isinstance(ev, AgentEvent)
        assert ev.schema_version == AGENT_EVENT_SCHEMA
        assert ev.runtime == "codex"
        assert ev.type == "session_started"
        # CC-compatible fields the reducer reads
        assert ev.payload["model"] == "gpt-5.6-sol"
        assert ev.payload["cwd"] == "/repo"
        assert ev.payload["cc_session_id"] == "thr-1"
        assert ev.payload["tools"] == []
        assert ev.payload["permission_profile"] == ":workspace-write"
        assert ev.payload["effective_sandbox"] == "workspace-write"
        assert ev.payload["effective_approval"] == "on-request"
        assert ev.payload["network_access"] is False

    def test_turn_started_maps_to_turn_start(self, adapter) -> None:
        """Codex turn_started (no -ed) → CC turn_start; revertible false."""

        ev = adapter.wrap(_codex(CodexEventType.TURN_STARTED, seq=2, item_id=None))
        assert ev.type == "turn_start"
        assert ev.turn_id == "turn-1"
        assert ev.payload["revertible"] is False

    def test_user_passthrough(self, adapter) -> None:
        ev = adapter.wrap(_codex(CodexEventType.USER, seq=3, payload={"text": "hi"}))
        assert ev.type == "user"
        assert ev.payload == {"text": "hi"}

    def test_assistant_delta_maps_to_text(self, adapter) -> None:
        """assistant_delta → text; payload.delta → payload.text."""

        ev = adapter.wrap(
            _codex(
                CodexEventType.ASSISTANT_DELTA,
                seq=4,
                item_id="item-1",
                payload={"delta": "hello "},
            )
        )
        assert ev.type == "text"
        assert ev.payload == {"text": "hello "}
        assert ev.item_id == "item-1"

    def test_reasoning_delta_maps_to_thinking(self, adapter) -> None:
        """reasoning_delta → thinking; delta → text."""

        ev = adapter.wrap(
            _codex(
                CodexEventType.REASONING_DELTA,
                seq=5,
                item_id="item-2",
                payload={"delta": "considering "},
            )
        )
        assert ev.type == "thinking"
        assert ev.payload["text"] == "considering "

    def test_assistant_message_dropped(self, adapter) -> None:
        """The final assistant_message duplicates streamed deltas → skip."""

        ev = adapter.wrap(
            _codex(
                CodexEventType.ASSISTANT_MESSAGE,
                seq=9,
                payload={"text": "full text", "phase": "done"},
            )
        )
        assert ev is None

    def test_tool_started_maps_to_tool_call(self, adapter) -> None:
        """commandExecution started → tool_call named 'command'."""

        ev = adapter.wrap(
            _codex(
                CodexEventType.TOOL_STARTED,
                seq=6,
                item_id="item-3",
                payload={
                    "kind": "commandExecution",
                    "command": "rg pattern",
                    "cwd": "/repo",
                    "source": "unifiedExecStartup",
                    "command_actions": (
                        {
                            "type": "search",
                            "command": "rg pattern",
                            "query": "pattern",
                            "path": ".",
                        },
                    ),
                    "started_at": 1234,
                },
            )
        )
        assert ev.type == "tool_call"
        assert ev.item_id == "item-3"
        assert ev.payload["tool_use_id"] == "item-3"
        assert ev.payload["tool_name"] == "command"
        assert ev.payload["input"] == {
            "command": "rg pattern",
            "cwd": "/repo",
            "source": "unifiedExecStartup",
            "command_actions": [
                {
                    "type": "search",
                    "command": "rg pattern",
                    "query": "pattern",
                    "path": ".",
                }
            ],
        }

    def test_tool_completed_maps_to_tool_result_with_exit_code(self, adapter) -> None:
        """command completed → tool_result carrying output/exit_code/duration."""

        ev = adapter.wrap(
            _codex(
                CodexEventType.TOOL_COMPLETED,
                seq=7,
                item_id="item-3",
                payload={
                    "kind": "commandExecution",
                    "command": "rg pattern",
                    "cwd": "/repo",
                    "status": "completed",
                    "exit_code": 0,
                    "output": "match.txt:1:hit",
                    "duration_ms": 12,
                    "completed_at": 5678,
                },
            )
        )
        assert ev.type == "tool_result"
        assert ev.item_id == "item-3"
        assert ev.payload["tool_use_id"] == "item-3"
        assert ev.payload["content"] == "match.txt:1:hit"
        assert ev.payload["exit_code"] == 0
        assert ev.payload["duration_ms"] == 12
        assert ev.payload["cwd"] == "/repo"


class TestExtensions:
    """Codex events with no CC equivalent keep their own type as extensions."""

    def test_usage_updated_passthrough(self, adapter) -> None:
        # Real fixture shape: total/last are token-breakdown OBJECTS, not numbers.
        ev = adapter.wrap(
            _codex(
                CodexEventType.USAGE_UPDATED,
                seq=8,
                payload={
                    "total": {
                        "totalTokens": 15495,
                        "inputTokens": 15475,
                        "cachedInputTokens": 9984,
                        "outputTokens": 20,
                        "reasoningOutputTokens": 0,
                    },
                    "last": {
                        "totalTokens": 15495,
                        "inputTokens": 15475,
                    },
                    "model_context_window": 258400,
                },
            )
        )
        assert ev.type == "usage_updated"
        assert ev.payload["total"]["totalTokens"] == 15495
        assert ev.payload["total"]["cachedInputTokens"] == 9984
        assert ev.payload["model_context_window"] == 258400

    def test_host_status_passthrough(self, adapter) -> None:
        ev = adapter.wrap(
            _codex(
                CodexEventType.HOST_STATUS,
                seq=10,
                thread_id=None,
                payload={"status": "host_exited", "reason": "eof", "exit_code": 1},
            )
        )
        assert ev.type == "host_status"
        assert ev.payload["status"] == "host_exited"
        assert ev.payload["exit_code"] == 1


class TestTerminalStates:
    """finished / interrupted / error map to the CC terminal vocabulary."""

    def test_finished_maps_with_null_cost_fields(self, adapter) -> None:
        """Codex finished has no cost/num_turns → nulls (usage via usage_updated)."""

        ev = adapter.wrap(
            _codex(
                CodexEventType.FINISHED,
                seq=11,
                payload={
                    "turn_id": "turn-1",
                    "status": "completed",
                    "duration_ms": 4200,
                },
            )
        )
        assert ev.type == "finished"
        assert ev.payload["total_cost_usd"] is None
        assert ev.payload["num_turns"] is None

    def test_interrupted_maps(self, adapter) -> None:
        ev = adapter.wrap(_codex(CodexEventType.INTERRUPTED, seq=12))
        assert ev.type == "interrupted"

    def test_native_error_maps_to_retrying(self, adapter) -> None:
        """kind=native_error → retrying (non-terminal; CodexSession waits for
        turn/completed, so the hub must NOT stop the stream on this)."""

        ev = adapter.wrap(
            _codex(
                CodexEventType.ERROR,
                seq=13,
                payload={
                    "kind": "native_error",
                    "error_type": "rate_limit",
                    "message": "slow down",
                    "will_retry": True,
                },
            )
        )
        assert ev.type == "retrying"
        assert ev.payload["error"] == "slow down"

    def test_turn_failed_maps_to_terminal_error(self, adapter) -> None:
        """turn/completed status=failed (no kind) → terminal error."""

        ev = adapter.wrap(
            _codex(
                CodexEventType.ERROR,
                seq=14,
                payload={
                    "turn_id": "turn-1",
                    "status": "failed",
                    "error": "model refused",
                    "duration_ms": 1000,
                },
            )
        )
        assert ev.type == "error"
        assert ev.payload["subclass"] == "turn_failed"
        assert ev.payload["errors"] == ["model refused"]


class TestEnvelopePassthrough:
    """session_id / runtime / turn_id pass through; seq is the adapter's own counter."""

    def test_turn_and_item_ids_pass_through(self, adapter) -> None:
        """Native turn_id / item_id survive onto the envelope for correlation."""

        ev = adapter.wrap(
            _codex(
                CodexEventType.ASSISTANT_DELTA,
                seq=42,
                turn_id="turn-9",
                item_id="item-7",
                payload={"delta": "x"},
            )
        )
        assert ev.turn_id == "turn-9"
        assert ev.item_id == "item-7"
        assert ev.session_id == "codex-sess"

    def test_seq_is_adapter_counter_not_native(self, adapter) -> None:
        """The adapter assigns a contiguous per-session seq, ignoring native seq.

        A dropped event (assistant_message) must not punch a hole: the next
        emitted event gets the very next seq, even though Codex's native seq
        advanced past the dropped one.
        """

        first = adapter.wrap(
            _codex(CodexEventType.ASSISTANT_DELTA, seq=10, payload={"delta": "a"})
        )
        # native seq 11 — dropped (assistant_message), no envelope emitted
        assert (
            adapter.wrap(
                _codex(CodexEventType.ASSISTANT_MESSAGE, seq=11, payload={"text": "a"})
            )
            is None
        )
        third = adapter.wrap(
            _codex(CodexEventType.ASSISTANT_DELTA, seq=12, payload={"delta": "b"})
        )
        assert first.seq == 1
        assert third.seq == 2  # contiguous — no hole from the dropped native seq 11
