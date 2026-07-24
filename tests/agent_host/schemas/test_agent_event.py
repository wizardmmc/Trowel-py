from __future__ import annotations

import pytest
from pydantic import ValidationError

from trowel_py.schemas.agent_host import (
    AGENT_EVENT_SCHEMA,
    AGENT_EVENT_TYPES,
    AgentEvent,
)


class TestEnvelopeShape:
    def test_json_schema_exposes_current_contract_description(self) -> None:
        assert AgentEvent.model_json_schema()["description"] == (
            "live stream 与 history replay 共用的 host-neutral envelope。\n\n"
            "``seq`` 只在同一 session 内比较，用于去重和发现缺口；``turn_id`` 与\n"
            "``item_id`` 在原生协议提供时保持关联语义。payload 的逐类型校验由各 "
            "runtime\ntranslator 负责，本模型只拒绝共享词汇之外的 ``type``。"
        )

    def test_minimal_event_serialises_to_v1_envelope(self) -> None:
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
        a = AgentEvent(session_id="s", runtime="codex", seq=1, type="status")
        b = AgentEvent(session_id="s", runtime="codex", seq=2, type="status")
        a.payload["x"] = 1
        assert b.payload == {}


class TestValidation:
    @pytest.mark.parametrize("bad_seq", [0, -1])
    def test_seq_must_be_positive(self, bad_seq: int) -> None:
        with pytest.raises(ValidationError):
            AgentEvent(session_id="s", runtime="codex", seq=bad_seq, type="text")

    def test_unknown_runtime_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentEvent(session_id="s", runtime="gemini", seq=1, type="text")

    def test_unknown_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentEvent(session_id="s", runtime="codex", seq=1, type="bogus_kind")

    def test_session_id_required(self) -> None:
        with pytest.raises(ValidationError):
            AgentEvent(runtime="codex", seq=1, type="text")  # type: ignore[call-arg]


class TestTypeVocabulary:
    def test_cc_types_are_in_vocabulary(self) -> None:
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
        assert "usage_updated" in AGENT_EVENT_TYPES
        assert "host_status" in AGENT_EVENT_TYPES
        assert "approval_request" in AGENT_EVENT_TYPES
        assert "rate_limit_updated" in AGENT_EVENT_TYPES

    def test_every_vocabulary_member_constructs(self) -> None:
        for t in sorted(AGENT_EVENT_TYPES):
            AgentEvent(session_id="s", runtime="codex", seq=1, type=t)
