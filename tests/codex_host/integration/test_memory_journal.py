"""真实 Codex app-server 到 memory turn journal 的最小 smoke。"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest

from trowel_py.codex_host import (
    CodexEventType,
    CodexHostManager,
    CodexSession,
    CodexSessionConfig,
)
from trowel_py.memory.codex_journal import CodexTurnJournal
from trowel_py.memory.sessions_repo import (
    create_sessions_repository,
    open_sessions_db,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("CODEX_INTEGRATION") != "1",
        reason="set CODEX_INTEGRATION=1 to run the real Codex journal smoke",
    ),
]


async def test_real_codex_turn_is_sealed_in_memory_journal() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="trowel-codex-journal-work-"))
    memory_root = Path(tempfile.mkdtemp(prefix="trowel-codex-journal-memory-"))
    manager = CodexHostManager()
    journal = CodexTurnJournal(
        memory_root,
        trowel_session_id="integration-journal",
        workdir=str(workdir),
        memory_enabled=True,
        profile_enabled=True,
    )
    session = CodexSession(
        CodexSessionConfig(
            "integration-journal",
            str(workdir),
            ephemeral=True,
        ),
        event_sink=journal.record,
    )
    manager.register(session)
    try:
        turn_id = await manager.send(session, "Reply with exactly: journal-ok")
        deadline = asyncio.get_running_loop().time() + 120
        events = []
        while asyncio.get_running_loop().time() < deadline:
            events.extend(session.drain())
            if any(
                event.type
                in {
                    CodexEventType.FINISHED,
                    CodexEventType.INTERRUPTED,
                    CodexEventType.ERROR,
                }
                for event in events
            ):
                break
            await asyncio.sleep(0.2)
        assert any(event.type is CodexEventType.FINISHED for event in events)

        conn = open_sessions_db(memory_root)
        try:
            [segment] = create_sessions_repository(conn).codex.claim_pending_fragments(
                completed_before="9999-12-31T00:00:00"
            )
        finally:
            conn.close()
        [turn] = segment.turns
        assert turn.turn_id == turn_id
        lines = [
            json.loads(line)
            for line in Path(turn.journal_path).read_text(encoding="utf-8").splitlines()
        ]
        assert lines[0]["type"] == "user"
        assert lines[-1]["type"] == "finished"
    finally:
        await manager.close()
        import shutil

        shutil.rmtree(workdir, ignore_errors=True)
        shutil.rmtree(memory_root, ignore_errors=True)
