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
            "表示 Claude Code 与 Codex 在实时事件流和历史回放中共用的事件。\n\n"
            "事件内容由对应运行工具的转换代码负责校验；本模型只检查事件类型是否"
            "已经登记。\n\n"
            "Attributes:\n"
            '    schema_version: 事件格式版本；序列化后的字段名为 schema，当前固定为\n'
            '        "agent-event-v1"。\n'
            "    session_id: 事件所属的 Trowel 会话 ID。\n"
            '    runtime: 产生事件的运行工具，值为 "claude_code" 或 "codex"。\n'
            "    seq: 该 Trowel 会话实际发出事件的连续序号，从 1 开始，用于去重和发现\n"
            "        事件缺失。\n"
            "    type: 事件类型，必须属于 AGENT_EVENT_TYPES。\n"
            "    thread_id: Codex thread ID；Claude Code 事件不使用该字段。\n"
            "    turn_id: 事件所属的轮次 ID。\n"
            "    item_id: 事件关联的工具调用或其他 Codex 条目 ID，用于关联同一条目的\n"
            "        启动、更新和完成事件。\n"
            "    payload: 随事件类型变化的具体内容。"
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
            "thread_id": None,
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
            thread_id="thread-2",
            turn_id="turn-9",
            item_id="item-3",
            payload={"content": "done"},
        )
        dumped = ev.model_dump(by_alias=True)
        assert dumped["turn_id"] == "turn-9"
        assert dumped["item_id"] == "item-3"
        assert dumped["thread_id"] == "thread-2"

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
        assert "goal_updated" in AGENT_EVENT_TYPES
        assert "goal_cleared" in AGENT_EVENT_TYPES
        assert "plan_updated" in AGENT_EVENT_TYPES
        assert "subagent_activity" in AGENT_EVENT_TYPES

    def test_every_vocabulary_member_constructs(self) -> None:
        for t in sorted(AGENT_EVENT_TYPES):
            AgentEvent(session_id="s", runtime="codex", seq=1, type=t)
