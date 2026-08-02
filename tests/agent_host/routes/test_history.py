import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.agent_host.routes.support import put_cc_binding
from trowel_py.agent_host.binding import Runtime, make_binding
from trowel_py.agent_host.hub import SessionHub
from trowel_py.agent_host.runtimes.claude_code import ClaudeCodeEventAdapter
from trowel_py.codex_host.events import CodexEvent, CodexEventType, immutable_payload
from trowel_py.codex_host.translator import CodexTranslator
from trowel_py.memory.codex_journal import CodexTurnJournal
from trowel_py.schemas.agent_host import AGENT_EVENT_SCHEMA
from trowel_py.schemas.cc_host import FinishedEvent, TextEvent, UserEvent


def test_get_history_cc_wraps_into_envelope(
    client: TestClient,
    hub: SessionHub,
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = put_cc_binding(
        hub,
        workdir,
        native_session_id="cc-native-9",
    )
    monkeypatch.setattr(
        "trowel_py.cc_host.history.parse_history",
        lambda workdir, cc_session_id: [
            UserEvent(text="hi"),
            TextEvent(text="hello back"),
            FinishedEvent(usage={}, total_cost_usd=0.001, num_turns=1),
        ],
    )

    response = client.get(f"/api/agent/sessions/{session_id}/history")

    assert response.status_code == 200
    events = response.json()["data"]
    assert [event["schema"] for event in events] == [AGENT_EVENT_SCHEMA] * 3
    assert [event["type"] for event in events] == [
        "user",
        "text",
        "finished",
    ]
    assert [event["seq"] for event in events] == [1, 2, 3]
    assert all(event["runtime"] == "claude_code" for event in events)


def test_get_history_cc_no_native_returns_empty(
    client: TestClient,
    hub: SessionHub,
    workdir: Path,
) -> None:
    session_id = put_cc_binding(
        hub,
        workdir,
        native_session_id=None,
    )

    response = client.get(f"/api/agent/sessions/{session_id}/history")

    assert response.status_code == 200
    assert response.json()["data"] == []


def test_get_history_codex_uses_thread_read_and_returns_replay(
    client: TestClient,
    hub: SessionHub,
    workdir: Path,
) -> None:
    binding = make_binding(
        session_id="history-codex",
        runtime=Runtime.CODEX,
        native_session_id="thread-1",
        workdir=str(workdir),
        model="gpt-5.6-sol",
        effort=None,
        permission=None,
        memory_enabled=True,
        profile_enabled=True,
        capabilities=("tools", "approval"),
        name="project",
    )
    hub.store.put(binding)
    hub._codex.thread_reads["thread-1"] = {  # type: ignore[union-attr]  # noqa: SLF001
        "id": "thread-1",
        "turns": [
            {
                "id": "turn-1",
                "status": "completed",
                "items": [
                    {
                        "id": "user-1",
                        "type": "userMessage",
                        "content": [{"type": "text", "text": "hi"}],
                    },
                    {
                        "id": "agent-1",
                        "type": "agentMessage",
                        "text": "hello back",
                    },
                ],
            }
        ],
    }

    response = client.get(f"/api/agent/sessions/{binding.session_id}/history")

    assert response.status_code == 200
    assert [event["type"] for event in response.json()["data"]] == [
        "user",
        "text",
        "finished",
    ]
    assert hub._codex.read_thread_calls == ["thread-1"]  # type: ignore[union-attr]  # noqa: SLF001


def test_get_history_codex_restores_recorded_command_executions(
    client: TestClient,
    hub: SessionHub,
    workdir: Path,
) -> None:
    thread_id = "thread-with-commands"
    turn_id = "turn-with-commands"
    memory_root = workdir.parent / "memory"
    binding = make_binding(
        session_id="history-codex-commands",
        runtime=Runtime.CODEX,
        native_session_id=thread_id,
        workdir=str(workdir),
        model="gpt-5.6-sol",
        effort=None,
        permission=None,
        memory_enabled=True,
        profile_enabled=True,
        capabilities=("tools", "approval"),
        name="project",
    )
    hub.store.put(binding)
    # 真实 thread/read 不含 commandExecution，只能返回消息和轮次终态。
    hub._codex.thread_reads[thread_id] = {  # type: ignore[union-attr]  # noqa: SLF001
        "id": thread_id,
        "turns": [
            {
                "id": turn_id,
                "status": "completed",
                "items": [
                    {
                        "id": "user-1",
                        "type": "userMessage",
                        "content": [{"type": "text", "text": "inspect files"}],
                    },
                    {
                        "id": "agent-1",
                        "type": "agentMessage",
                        "text": "inspection complete",
                    },
                ],
            }
        ],
    }

    journal = CodexTurnJournal(
        memory_root,
        trowel_session_id=binding.session_id,
        workdir=str(workdir),
        memory_enabled=True,
        profile_enabled=True,
    )
    recorded: list[CodexEvent] = [
        CodexEvent(
            binding.session_id,
            1,
            CodexEventType.USER,
            thread_id,
            turn_id,
            payload=immutable_payload(text="inspect files"),
        ),
        CodexEvent(
            binding.session_id,
            2,
            CodexEventType.TURN_STARTED,
            thread_id,
            turn_id,
            payload=immutable_payload(autonomous=False),
        ),
    ]
    fixture = (
        Path(__file__).parents[2] / "codex_host" / "fixtures" / "command-actions.jsonl"
    )
    translator = CodexTranslator()
    for line in fixture.read_text(encoding="utf-8").splitlines():
        message = json.loads(line)
        message["params"]["threadId"] = thread_id
        message["params"]["turnId"] = turn_id
        for item in translator.translate(message["method"], message["params"]):
            recorded.append(
                CodexEvent(
                    binding.session_id,
                    len(recorded) + 1,
                    item.type,
                    item.thread_id,
                    item.turn_id,
                    item.item_id,
                    item.payload,
                )
            )
    recorded.extend(
        [
            CodexEvent(
                binding.session_id,
                len(recorded) + 1,
                CodexEventType.ASSISTANT_DELTA,
                thread_id,
                turn_id,
                "agent-1",
                immutable_payload(delta="inspection complete"),
            ),
            CodexEvent(
                binding.session_id,
                len(recorded) + 2,
                CodexEventType.FINISHED,
                thread_id,
                turn_id,
                payload=immutable_payload(status="completed"),
            ),
        ]
    )
    for event in recorded:
        journal.record(event, None)

    response = client.get(f"/api/agent/sessions/{binding.session_id}/history")

    assert response.status_code == 200
    events = response.json()["data"]
    assert [event["type"] for event in events].count("tool_call") == 3
    assert [event["type"] for event in events].count("tool_result") == 3
    assert [event["type"] for event in events].count("user") == 1
    assert [event["type"] for event in events].count("text") == 1
    assert [
        event["payload"]["input"]["command_actions"][0]["type"]
        for event in events
        if event["type"] == "tool_call"
    ] == ["listFiles", "read", "search"]


def test_get_history_unknown_session_404(client: TestClient) -> None:
    response = client.get("/api/agent/sessions/unknown/history")

    assert response.status_code == 404


def test_get_codex_child_history_checks_parent_and_returns_child_events(
    client: TestClient,
    hub: SessionHub,
    workdir: Path,
) -> None:
    binding = make_binding(
        session_id="history-parent",
        runtime=Runtime.CODEX,
        native_session_id="parent-thread-1",
        workdir=str(workdir),
        model="gpt-5.6-sol",
        effort=None,
        permission=None,
        memory_enabled=True,
        profile_enabled=True,
        capabilities=("tools", "approval", "subagents"),
        name="project",
    )
    hub.store.put(binding)
    hub._codex.thread_reads["child-thread-1"] = {  # type: ignore[union-attr]  # noqa: SLF001
        "id": "child-thread-1",
        "parentThreadId": "parent-thread-1",
        "turns": [
            {
                "id": "parent-turn-1",
                "status": "completed",
                "items": [
                    {
                        "id": "inherited-user-1",
                        "type": "userMessage",
                        "content": [{"type": "text", "text": "parent prompt"}],
                    }
                ],
            },
            {
                "id": "child-turn-1",
                "status": "completed",
                "items": [
                    {
                        "id": "child-message-1",
                        "type": "agentMessage",
                        "text": "child result",
                    }
                ],
            },
        ],
    }
    hub._codex.thread_reads["parent-thread-1"] = {  # type: ignore[union-attr]  # noqa: SLF001
        "id": "parent-thread-1",
        "turns": [{"id": "parent-turn-1", "status": "completed", "items": []}],
    }

    response = client.get(
        f"/api/agent/sessions/{binding.session_id}/subagents/child-thread-1/history"
    )

    assert response.status_code == 200
    events = response.json()["data"]
    assert [event["type"] for event in events] == [
        "turn_start",
        "text",
        "finished",
    ]
    assert all(event["thread_id"] == "child-thread-1" for event in events)
    assert all(event["payload"].get("text") != "parent prompt" for event in events)

    hub._codex.thread_reads["child-thread-1"]["parentThreadId"] = "other-root"  # type: ignore[union-attr]  # noqa: SLF001
    hub._codex.thread_reads["other-root"] = {  # type: ignore[union-attr]  # noqa: SLF001
        "id": "other-root",
        "parentThreadId": None,
        "turns": [],
    }
    denied = client.get(
        f"/api/agent/sessions/{binding.session_id}/subagents/child-thread-1/history"
    )
    assert denied.status_code == 403

    hub._codex.thread_reads["child-thread-1"]["parentThreadId"] = "middle-thread"  # type: ignore[union-attr]  # noqa: SLF001
    hub._codex.thread_reads["middle-thread"] = {  # type: ignore[union-attr]  # noqa: SLF001
        "id": "middle-thread",
        "parentThreadId": "parent-thread-1",
        "turns": [],
    }
    nested = client.get(
        f"/api/agent/sessions/{binding.session_id}/subagents/child-thread-1/history"
    )
    assert nested.status_code == 200


def test_list_history_invalid_cursor_returns_400(
    client: TestClient,
    workdir: Path,
) -> None:
    response = client.get(
        "/api/agent/sessions",
        params={"workdir": str(workdir), "cursor": "not-a-cursor"},
    )

    assert response.status_code == 400


def test_error_envelope_uses_per_session_seq_not_fixed_one(
    hub: SessionHub,
    workdir: Path,
) -> None:
    session_id = put_cc_binding(
        hub,
        workdir,
        native_session_id="cc-native-error",
    )
    adapter = ClaudeCodeEventAdapter(session_id)
    for _ in range(3):
        adapter.wrap(TextEvent(text="x").model_dump())
    hub._cc_adapters[session_id] = adapter  # noqa: SLF001

    envelope = hub.error_envelope(session_id, "boom")

    assert envelope["type"] == "error"
    assert envelope["payload"]["errors"] == ["boom"]
    assert envelope["seq"] == 4
    assert envelope["runtime"] == "claude_code"
