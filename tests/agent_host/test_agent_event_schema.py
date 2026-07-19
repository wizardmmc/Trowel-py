"""Tests for the AgentEvent v1 envelope (slice-074).

The envelope is the single wire shape both runtimes emit after slice-074; the
frontend consumes it everywhere (live stream + history). These tests pin the
field set, the discriminator vocabulary, and the fail-fast validation at the
SSE boundary (spec C-1: never trust shape blindly, even from our own adapter).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trowel_py.schemas.agent_host import (
    AGENT_EVENT_SCHEMA,
    AGENT_EVENT_TYPES,
    AgentEvent,
)


class TestEnvelopeShape:
    """The envelope's wire fields + JSON key names."""

    def test_minimal_event_serialises_to_v1_envelope(self) -> None:
        """A bare event carries schema/session_id/runtime/seq/type + defaults."""

        ev = AgentEvent(
            session_id="s1",
            runtime="codex",
            seq=1,
            type="text",
            payload={"text": "hi"},
        )
        dumped = ev.model_dump(by_alias=True)
        assert dumped == {
            "schema": AGENT_EVENT_SCHEMA,
            "session_id": "s1",
            "runtime": "codex",
            "seq": 1,
            "type": "text",
            "turn_id": None,
            "item_id": None,
            "payload": {"text": "hi"},
        }

    def test_full_event_carries_turn_and_item_ids(self) -> None:
        """turn_id/item_id surface native ids for delta↔completed correlation."""

        ev = AgentEvent(
            session_id="s1",
            runtime="codex",
            seq=7,
            type="tool_result",
            turn_id="turn-9",
            item_id="item-3",
            payload={"content": "done"},
        )
        dumped = ev.model_dump(by_alias=True)
        assert dumped["turn_id"] == "turn-9"
        assert dumped["item_id"] == "item-3"

    def test_payload_defaults_to_empty_dict_not_shared(self) -> None:
        """Each event gets its own payload dict (no mutable default aliasing)."""

        a = AgentEvent(session_id="s", runtime="codex", seq=1, type="status")
        b = AgentEvent(session_id="s", runtime="codex", seq=2, type="status")
        a.payload["x"] = 1
        assert b.payload == {}


class TestValidation:
    """Fail-fast at the envelope boundary."""

    @pytest.mark.parametrize("bad_seq", [0, -1])
    def test_seq_must_be_positive(self, bad_seq: int) -> None:
        """seq starts at 1 and is monotonic per session; 0/negative is invalid."""

        with pytest.raises(ValidationError):
            AgentEvent(
                session_id="s", runtime="codex", seq=bad_seq, type="text"
            )

    def test_unknown_runtime_rejected(self) -> None:
        """runtime is a closed Literal (claude_code | codex)."""

        with pytest.raises(ValidationError):
            AgentEvent(
                session_id="s", runtime="gemini", seq=1, type="text"
            )

    def test_unknown_type_rejected(self) -> None:
        """An unmapped type is an adapter bug, not a passthrough — reject it."""

        with pytest.raises(ValidationError):
            AgentEvent(
                session_id="s", runtime="codex", seq=1, type="bogus_kind"
            )

    def test_session_id_required(self) -> None:
        """Every event must belong to a trowel session (spec §2)."""

        with pytest.raises(ValidationError):
            AgentEvent(runtime="codex", seq=1, type="text")  # type: ignore[call-arg]


class TestTypeVocabulary:
    """The discriminator set is the TrowelEvent contract + Codex extensions."""

    def test_cc_types_are_in_vocabulary(self) -> None:
        """CC's existing rich type names survive inside the v1 envelope."""

        for t in (
            "session_started",
            "turn_start",
            "user",
            "text",
            "thinking",
            "tool_call",
            "tool_progress",
            "tool_result",
            "finished",
            "error",
            "interrupted",
            "session_exited",
            "workflow_tree",
            "elicit_request",
        ):
            assert t in AGENT_EVENT_TYPES

    def test_codex_extensions_are_in_vocabulary(self) -> None:
        """Codex events with no CC equivalent surface as named extensions."""

        assert "usage_updated" in AGENT_EVENT_TYPES
        assert "host_status" in AGENT_EVENT_TYPES

    def test_every_vocabulary_member_constructs(self) -> None:
        """Smoke: each documented type is accepted by the validator."""

        for t in sorted(AGENT_EVENT_TYPES):
            AgentEvent(session_id="s", runtime="codex", seq=1, type=t)
