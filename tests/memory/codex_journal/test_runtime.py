from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from trowel_py.codex_host.events import (
    CodexEventType,
    TranslatedItem,
    immutable_payload,
)
from trowel_py.codex_host.session import CodexSession
from trowel_py.codex_host.session_types import CodexSessionConfig
from trowel_py.codex_host.translator import CodexTranslator
from trowel_py.memory.codex_journal import (
    CodexTurnJournal,
    recover_sealed_codex_turns,
)
from trowel_py.memory.sessions_repo import (
    SessionsRepository,
    create_sessions_repository,
    open_sessions_db,
)

THREAD_ID = "00000000-0000-7000-8000-000000000011"
TURN_ID = "00000000-0000-7000-8000-000000000012"


def _binding(thread_id: str = THREAD_ID) -> dict:
    return {
        "thread": {"id": thread_id},
        "model": "gpt-5.6-sol",
        "modelProvider": "openai",
        "cwd": "/workspace",
        "sandbox": {"type": "workspaceWrite"},
        "approvalPolicy": "on-request",
        "reasoningEffort": "high",
    }


def _real_notifications() -> list[dict]:
    fixture = (
        Path(__file__).parents[2] / "codex_host" / "fixtures" / "notifications.jsonl"
    )
    return [
        json.loads(line.replace("<uuid>", TURN_ID))
        for line in fixture.read_text(encoding="utf-8").splitlines()
    ]


def _real_mcp_item() -> dict:
    fixture = (
        Path(__file__).parents[2]
        / "codex_host"
        / "fixtures"
        / "thread-read-0.144.0.json"
    )
    thread = json.loads(fixture.read_text(encoding="utf-8"))["thread"]
    return next(
        item for item in thread["turns"][0]["items"] if item["type"] == "mcpToolCall"
    )


def _real_file_change_events() -> list[dict]:
    fixture = (
        Path(__file__).parents[2]
        / "codex_host"
        / "fixtures"
        / "file-change-add-modify-076.jsonl"
    )
    return [
        json.loads(line) for line in fixture.read_text(encoding="utf-8").splitlines()
    ]


def test_real_normalized_events_are_durable_before_turn_is_sealed(
    tmp_path: Path,
) -> None:
    ticks = iter(datetime(2026, 7, 23, 23, 58, second) for second in range(20))
    journal = CodexTurnJournal(
        tmp_path,
        trowel_session_id="trowel-codex-1",
        workdir="/workspace",
        memory_enabled=False,
        profile_enabled=True,
        now_fn=lambda: next(ticks),
    )
    session = CodexSession(
        CodexSessionConfig(
            trowel_session_id="trowel-codex-1",
            workdir="/workspace",
        ),
        event_sink=journal.record,
    )
    session.attach_thread_binding(_binding())
    session.begin_send()
    session.record_turn_started(TURN_ID, "inspect the repository")
    translator = CodexTranslator()

    notifications = _real_notifications()
    for message in notifications[:4]:
        message["params"]["threadId"] = THREAD_ID
        message["params"]["turnId"] = TURN_ID
        for item in translator.translate(message["method"], message["params"]):
            session.emit_translated(item)

    mcp = _real_mcp_item()
    for method in ("item/started", "item/completed"):
        for item in translator.translate(
            method,
            {"threadId": THREAD_ID, "turnId": TURN_ID, "item": mcp},
        ):
            session.emit_translated(item)

    for message in _real_file_change_events()[:-1]:
        params = json.loads(
            json.dumps(message["params"])
            .replace(message["params"]["threadId"], THREAD_ID)
            .replace(message["params"]["turnId"], TURN_ID)
        )
        for item in translator.translate(message["method"], params):
            session.emit_translated(item)

    terminal = notifications[8]
    terminal["params"]["threadId"] = THREAD_ID
    terminal["params"]["turn"]["id"] = TURN_ID
    for item in translator.translate(terminal["method"], terminal["params"]):
        session.emit_translated(item)

    conn = open_sessions_db(tmp_path)
    try:
        repo = create_sessions_repository(conn)
        [segment] = repo.find_incremental_codex(completed_before="2026-07-24T00:00:00")
    finally:
        conn.close()

    [turn] = segment.turns
    assert turn.thread_id == THREAD_ID
    assert turn.turn_id == TURN_ID
    assert turn.trowel_session_id == "trowel-codex-1"
    assert turn.memory_enabled is False
    assert turn.model == "gpt-5.6-sol"
    assert turn.effort == "high"
    lines = [
        json.loads(line)
        for line in Path(turn.journal_path).read_text(encoding="utf-8").splitlines()
    ]
    types = [line["type"] for line in lines]
    tool_kinds = [
        (line["type"], line["payload"].get("kind"))
        for line in lines
        if line["type"] in {"tool_started", "tool_completed"}
    ]
    assert ("tool_started", "commandExecution") in tool_kinds
    assert ("tool_completed", "commandExecution") in tool_kinds
    assert ("tool_started", "mcpToolCall") in tool_kinds
    assert ("tool_completed", "mcpToolCall") in tool_kinds
    assert ("tool_started", "fileChange") in tool_kinds
    assert ("tool_completed", "fileChange") in tool_kinds
    assert types[-1] == "finished"


def test_memory_ineligible_autonomous_turn_is_not_registered(tmp_path: Path) -> None:
    journal = CodexTurnJournal(
        tmp_path,
        trowel_session_id="trowel-native-command",
        workdir="/workspace",
        memory_enabled=True,
        profile_enabled=True,
    )
    session = CodexSession(
        CodexSessionConfig("trowel-native-command", "/workspace"),
        event_sink=journal.record,
    )
    session.attach_thread_binding(_binding())
    session.begin_send(autonomous=True, memory_eligible=False)
    session.record_native_turn_started(TURN_ID)
    session.emit_translated(
        TranslatedItem(
            type=CodexEventType.FINISHED,
            thread_id=THREAD_ID,
            turn_id=TURN_ID,
            payload=immutable_payload(status="completed"),
        )
    )

    conn = open_sessions_db(tmp_path)
    try:
        repo = create_sessions_repository(conn)
        assert repo.find_incremental_codex() == []
        assert repo.find_unsealed_codex_turns() == []
    finally:
        conn.close()
    assert list((tmp_path / "meta" / "codex-turns").rglob("*.jsonl")) == []


def test_unsolicited_autonomous_turn_remains_memory_eligible(tmp_path: Path) -> None:
    journal = CodexTurnJournal(
        tmp_path,
        trowel_session_id="trowel-autonomous",
        workdir="/workspace",
        memory_enabled=True,
        profile_enabled=True,
    )
    session = CodexSession(
        CodexSessionConfig("trowel-autonomous", "/workspace"),
        event_sink=journal.record,
    )
    session.attach_thread_binding(_binding())
    session.record_native_turn_started(TURN_ID)
    session.emit_translated(
        TranslatedItem(
            type=CodexEventType.FINISHED,
            thread_id=THREAD_ID,
            turn_id=TURN_ID,
            payload=immutable_payload(status="completed"),
        )
    )

    conn = open_sessions_db(tmp_path)
    try:
        [segment] = create_sessions_repository(conn).find_incremental_codex()
    finally:
        conn.close()
    assert segment.turn_ids == (TURN_ID,)


def test_same_thread_completed_turns_form_one_pending_fragment(tmp_path: Path) -> None:
    conn = open_sessions_db(tmp_path)
    try:
        repo = create_sessions_repository(conn)
        repo.register_codex_turn(
            thread_id="thread-1",
            turn_id="turn-1",
            trowel_session_id="trowel-1",
            workdir="/workspace",
            journal_path="/journal/turn-1.jsonl",
            registered_at="2026-07-22T10:00:00",
            model="gpt-5.6-sol",
            effort="high",
            provider="openai",
            memory_enabled=True,
            profile_enabled=True,
        )
        repo.complete_codex_turn(
            "thread-1",
            "turn-1",
            status="completed",
            completed_at="2026-07-22T10:05:00",
        )
        repo.register_codex_turn(
            thread_id="thread-1",
            turn_id="turn-2",
            trowel_session_id="trowel-2",
            workdir="/workspace",
            journal_path="/journal/turn-2.jsonl",
            registered_at="2026-07-23T10:00:00",
            model="gpt-5.6-sol",
            effort="high",
            provider="openai",
            memory_enabled=True,
            profile_enabled=True,
        )
        repo.complete_codex_turn(
            "thread-1",
            "turn-2",
            status="completed",
            completed_at="2026-07-23T10:05:00",
        )

        all_pending = repo.find_incremental_codex(
            completed_before="2026-07-24T00:00:00"
        )
        repo.advance_codex_extracted_many(
            "thread-1",
            ("turn-1", "turn-2"),
            when="2026-07-24T02:30:00",
        )
        after_advance = repo.find_incremental_codex(
            completed_before="2026-07-25T00:00:00"
        )
    finally:
        conn.close()

    assert [item.turn_ids for item in all_pending] == [("turn-1", "turn-2")]
    assert after_advance == []


def test_failed_fragment_membership_does_not_absorb_later_turn(
    tmp_path: Path,
) -> None:
    conn = open_sessions_db(tmp_path)
    try:
        repo = create_sessions_repository(conn)
        for index in (1, 2):
            repo.register_codex_turn(
                thread_id="thread-1",
                turn_id=f"turn-{index}",
                trowel_session_id=f"trowel-{index}",
                workdir="/workspace",
                journal_path=f"/journal/turn-{index}.jsonl",
                registered_at=f"2026-07-22T10:0{index}:00",
                model="gpt-5.6-sol",
                effort="high",
                provider="openai",
                memory_enabled=True,
                profile_enabled=True,
            )
            repo.complete_codex_turn(
                "thread-1",
                f"turn-{index}",
                status="completed",
                completed_at=f"2026-07-22T10:0{index}:30",
            )

        [claimed] = repo.find_incremental_codex()

        repo.register_codex_turn(
            thread_id="thread-1",
            turn_id="turn-3",
            trowel_session_id="trowel-3",
            workdir="/workspace",
            journal_path="/journal/turn-3.jsonl",
            registered_at="2026-07-22T10:03:00",
            model="gpt-5.6-sol",
            effort="high",
            provider="openai",
            memory_enabled=True,
            profile_enabled=True,
        )
        repo.complete_codex_turn(
            "thread-1",
            "turn-3",
            status="completed",
            completed_at="2026-07-22T10:03:30",
        )
        retried = repo.find_incremental_codex()
    finally:
        conn.close()

    assert claimed.turn_ids == ("turn-1", "turn-2")
    assert [(item.fragment_id, item.turn_ids) for item in retried] == [
        (claimed.fragment_id, ("turn-1", "turn-2")),
        (retried[1].fragment_id, ("turn-3",)),
    ]
    assert retried[1].fragment_id != claimed.fragment_id


def test_codex_fragment_keeps_cutoff_and_user_session_kind_filters(
    tmp_path: Path,
) -> None:
    conn = open_sessions_db(tmp_path)
    try:
        repo = create_sessions_repository(conn)
        cases = (
            ("eligible-user", "2026-07-22T10:00:00", "user"),
            ("future-user", "2026-07-24T10:00:00", "user"),
            ("eligible-delegate", "2026-07-22T11:00:00", "delegate"),
        )
        for turn_id, completed_at, session_kind in cases:
            repo.register_codex_turn(
                thread_id="thread-1",
                turn_id=turn_id,
                trowel_session_id=f"trowel-{turn_id}",
                workdir="/workspace",
                journal_path=f"/journal/{turn_id}.jsonl",
                registered_at=completed_at,
                model="gpt-5.6-sol",
                effort="high",
                provider="openai",
                memory_enabled=True,
                profile_enabled=True,
                session_kind=session_kind,
            )
            repo.complete_codex_turn(
                "thread-1",
                turn_id,
                status="completed",
                completed_at=completed_at,
            )

        [fragment] = repo.find_incremental_codex(completed_before="2026-07-23T00:00:00")
        rows = conn.execute(
            "SELECT turn_id, review_fragment_id FROM codex_turns ORDER BY turn_id"
        ).fetchall()
    finally:
        conn.close()

    assert fragment.turn_ids == ("eligible-user",)
    fragment_ids = {row["turn_id"]: row["review_fragment_id"] for row in rows}
    assert fragment_ids["future-user"] == ""
    assert fragment_ids["eligible-delegate"] == ""


def test_atomic_codex_fragment_advance_rolls_back_if_any_turn_is_missing(
    tmp_path: Path,
) -> None:
    conn = open_sessions_db(tmp_path)
    try:
        repo = create_sessions_repository(conn)
        repo.register_codex_turn(
            thread_id="thread-1",
            turn_id="turn-1",
            trowel_session_id="trowel-1",
            workdir="/workspace",
            journal_path="/journal/turn-1.jsonl",
            registered_at="2026-07-22T10:00:00",
            model="gpt-5.6-sol",
            effort="high",
            provider="openai",
            memory_enabled=True,
            profile_enabled=True,
        )
        repo.complete_codex_turn(
            "thread-1",
            "turn-1",
            status="completed",
            completed_at="2026-07-22T10:01:00",
        )

        with pytest.raises(ValueError, match="atomic"):
            repo.advance_codex_extracted_many(
                "thread-1",
                ("turn-1", "missing-turn"),
                when="2026-07-22T11:00:00",
            )
        [pending] = repo.find_incremental_codex()
    finally:
        conn.close()

    assert pending.turn_ids == ("turn-1",)


def test_legacy_single_turn_advance_cannot_partially_advance_fragment(
    tmp_path: Path,
) -> None:
    conn = open_sessions_db(tmp_path)
    try:
        repo = create_sessions_repository(conn)
        for index in (1, 2):
            repo.register_codex_turn(
                thread_id="thread-1",
                turn_id=f"turn-{index}",
                trowel_session_id=f"trowel-{index}",
                workdir="/workspace",
                journal_path=f"/journal/turn-{index}.jsonl",
                registered_at=f"2026-07-22T10:0{index}:00",
                model="gpt-5.6-sol",
                effort="high",
                provider="openai",
                memory_enabled=True,
                profile_enabled=True,
            )
            repo.complete_codex_turn(
                "thread-1",
                f"turn-{index}",
                status="completed",
                completed_at=f"2026-07-22T10:0{index}:30",
            )
        [fragment] = repo.find_incremental_codex()

        repo.advance_codex_extracted(
            "thread-1",
            "turn-1",
            when="2026-07-22T11:00:00",
        )
        pending = repo.find_incremental_codex()
    finally:
        conn.close()

    assert fragment.turn_ids == ("turn-1", "turn-2")
    assert pending == []


def test_fsynced_terminal_repairs_watermark_after_commit_crash(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = CodexTurnJournal(
        tmp_path,
        trowel_session_id="trowel-crash",
        workdir="/workspace",
        memory_enabled=True,
        profile_enabled=True,
        now_fn=lambda: datetime(2026, 7, 23, 23, 59, 59),
    )
    session = CodexSession(
        CodexSessionConfig("trowel-crash", "/workspace"),
        event_sink=journal.record,
    )
    session.attach_thread_binding(_binding())
    session.begin_send()
    session.record_turn_started(TURN_ID, "finish despite journal commit crash")
    original_complete = SessionsRepository.complete_codex_turn
    crashed = False

    def crash_once(self, *args, **kwargs):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise OSError("simulated crash after fsync")
        return original_complete(self, *args, **kwargs)

    monkeypatch.setattr(SessionsRepository, "complete_codex_turn", crash_once)
    terminal = _real_notifications()[8]
    terminal["params"]["threadId"] = THREAD_ID
    terminal["params"]["turn"]["id"] = TURN_ID
    for item in CodexTranslator().translate(terminal["method"], terminal["params"]):
        session.emit_translated(item)

    # memory commit 失败不能吞掉用户 turn 的 terminal。
    assert session.drain()[-1].type.value == "finished"
    conn = open_sessions_db(tmp_path)
    try:
        assert create_sessions_repository(conn).find_incremental_codex() == []
    finally:
        conn.close()

    assert recover_sealed_codex_turns(tmp_path) == 1
    conn = open_sessions_db(tmp_path)
    try:
        [recovered] = create_sessions_repository(conn).find_incremental_codex()
    finally:
        conn.close()
    assert recovered.turn_ids == (TURN_ID,)


def test_event_write_failure_cannot_be_recovered_as_complete(
    tmp_path: Path,
    monkeypatch,
) -> None:
    journal = CodexTurnJournal(
        tmp_path,
        trowel_session_id="trowel-write-failure",
        workdir="/workspace",
        memory_enabled=True,
        profile_enabled=True,
    )
    original_append = journal._append  # noqa: SLF001
    failed = False

    def fail_first_append(*args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("simulated journal write failure")
        return original_append(*args, **kwargs)

    monkeypatch.setattr(journal, "_append", fail_first_append)
    session = CodexSession(
        CodexSessionConfig("trowel-write-failure", "/workspace"),
        event_sink=journal.record,
    )
    session.attach_thread_binding(_binding())
    session.begin_send()
    session.record_turn_started(TURN_ID, "this user event fails to persist")
    terminal = _real_notifications()[8]
    terminal["params"]["threadId"] = THREAD_ID
    terminal["params"]["turn"]["id"] = TURN_ID
    for item in CodexTranslator().translate(terminal["method"], terminal["params"]):
        session.emit_translated(item)

    assert recover_sealed_codex_turns(tmp_path) == 0
    conn = open_sessions_db(tmp_path)
    try:
        assert create_sessions_repository(conn).find_incremental_codex() == []
    finally:
        conn.close()
