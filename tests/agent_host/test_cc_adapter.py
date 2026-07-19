"""Tests for the CC → AgentEvent v1 adapter (slice-074).

The adapter is the outer wrapper spec §2 calls for: CC's existing
translator/history keeps producing :mod:`trowel_py.schemas.cc_host` TrowelEvent
models, and this adapter wraps each ``model_dump()`` into the unified envelope
without touching the translator. CC events are constructed here from the real
Pydantic contract models (not synthesised) so the field shapes under test are
the actual wire contract.
"""

from __future__ import annotations

import pytest

from trowel_py.agent_host.cc_adapter import CcEventAdapter
from trowel_py.schemas.agent_host import AGENT_EVENT_SCHEMA, AgentEvent
from trowel_py.schemas.cc_host import (
    FinishedEvent,
    SessionStartedEvent,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
    TurnStartEvent,
)


def _dump(model: object) -> dict:
    """A CC TrowelEvent model_dump — the shape the adapter receives."""

    return model.model_dump()  # type: ignore[attr-defined]


class TestEnvelopeWrapping:
    """Every CC event becomes a well-formed AgentEvent v1 envelope."""

    def test_session_started_wraps_with_seq_1(self) -> None:
        """The first event on a session gets seq 1 and runtime claude_code."""

        adapter = CcEventAdapter(session_id="cc-1")
        ev = adapter.wrap(
            _dump(
                SessionStartedEvent(
                    model="glm-5.2",
                    cwd="/repo",
                    cc_session_id="cc-sess-7",
                    tools=["Read", "Write"],
                )
            )
        )
        assert isinstance(ev, AgentEvent)
        assert ev.schema_version == AGENT_EVENT_SCHEMA
        assert ev.session_id == "cc-1"
        assert ev.runtime == "claude_code"
        assert ev.seq == 1
        assert ev.type == "session_started"
        assert ev.turn_id is None
        assert ev.item_id is None
        # payload carries the CC fields verbatim (minus `type`)
        assert ev.payload["model"] == "glm-5.2"
        assert ev.payload["cwd"] == "/repo"
        assert ev.payload["cc_session_id"] == "cc-sess-7"
        assert ev.payload["tools"] == ["Read", "Write"]

    def test_turn_start_stamps_turn_id(self) -> None:
        """turn_start is the one CC event that carries a native turn id."""

        adapter = CcEventAdapter(session_id="cc-1")
        ev = adapter.wrap(
            _dump(TurnStartEvent(turn_id="turn-42", revertible=True))
        )
        assert ev.type == "turn_start"
        assert ev.turn_id == "turn-42"
        assert ev.payload["revertible"] is True

    def test_text_payload_carries_text(self) -> None:
        """text event: payload.text is the streamed fragment."""

        adapter = CcEventAdapter(session_id="cc-1")
        ev = adapter.wrap(_dump(TextEvent(text="hello ")))
        assert ev.type == "text"
        assert ev.payload == {"text": "hello "}
        assert ev.turn_id is None
        assert ev.item_id is None

    def test_tool_call_item_id_is_tool_use_id(self) -> None:
        """tool_use_id is the stable item id across call/progress/result."""

        adapter = CcEventAdapter(session_id="cc-1")
        ev = adapter.wrap(
            _dump(
                ToolCallEvent(
                    tool_use_id="tool-9",
                    tool_name="Bash",
                    input={"command": "pwd"},
                )
            )
        )
        assert ev.type == "tool_call"
        assert ev.item_id == "tool-9"
        # tool_use_id also survives inside payload (the reducer reads it there)
        assert ev.payload["tool_use_id"] == "tool-9"
        assert ev.payload["tool_name"] == "Bash"
        assert ev.payload["input"] == {"command": "pwd"}

    def test_tool_result_item_id_matches_its_tool_call(self) -> None:
        """A tool_result reuses the same item id as its tool_call (spec §2)."""

        adapter = CcEventAdapter(session_id="cc-1")
        adapter.wrap(
            _dump(
                ToolCallEvent(
                    tool_use_id="tool-9", tool_name="Bash", input={"command": "pwd"}
                )
            )
        )
        result = adapter.wrap(
            _dump(ToolResultEvent(tool_use_id="tool-9", content="/repo"))
        )
        assert result.type == "tool_result"
        assert result.item_id == "tool-9"
        assert result.payload["content"] == "/repo"


class TestSeqCounter:
    """seq is per-session, monotonic, starts at 1."""

    def test_seq_increments_across_events(self) -> None:
        """A stream of events stamps 1, 2, 3, … on the same adapter."""

        adapter = CcEventAdapter(session_id="cc-1")
        seqs = [
            adapter.wrap(_dump(TextEvent(text="a"))).seq,
            adapter.wrap(_dump(TextEvent(text="b"))).seq,
            adapter.wrap(_dump(TextEvent(text="c"))).seq,
        ]
        assert seqs == [1, 2, 3]

    def test_seq_is_per_session_not_shared(self) -> None:
        """Two adapters (two sessions) each start at 1 — cross-session never compared."""

        a = CcEventAdapter(session_id="cc-1")
        b = CcEventAdapter(session_id="cc-2")
        assert a.wrap(_dump(TextEvent(text="a"))).seq == 1
        assert b.wrap(_dump(TextEvent(text="b"))).seq == 1
        assert a.wrap(_dump(TextEvent(text="c"))).seq == 2

    def test_finished_does_not_reset_seq(self) -> None:
        """seq spans turns within a session — finished is just another event."""

        adapter = CcEventAdapter(session_id="cc-1")
        adapter.wrap(_dump(TextEvent(text="a")))  # seq 1
        adapter.wrap(
            _dump(
                FinishedEvent(
                    usage={}, total_cost_usd=0.001, num_turns=1
                )
            )
        )  # seq 2
        assert adapter.wrap(_dump(TextEvent(text="b"))).seq == 3


class TestPayloadIsolation:
    """The adapter never aliases the input dict into the envelope."""

    def test_mutating_input_after_wrap_does_not_affect_envelope(self) -> None:
        """The payload is a copy — callers can't mutate the envelope via the original."""

        adapter = CcEventAdapter(session_id="cc-1")
        original = _dump(TextEvent(text="hi"))
        ev = adapter.wrap(original)
        original["text"] = "TAMPERED"
        assert ev.payload["text"] == "hi"

    def test_type_is_not_duplicated_in_payload(self) -> None:
        """`type` lives on the envelope, not duplicated inside payload."""

        adapter = CcEventAdapter(session_id="cc-1")
        ev = adapter.wrap(_dump(TextEvent(text="hi")))
        assert "type" not in ev.payload
