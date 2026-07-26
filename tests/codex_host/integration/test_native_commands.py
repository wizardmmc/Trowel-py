"""真实 Codex 0.144.0 原生命令与通知链 smoke。"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from trowel_py.codex_host import (
    CodexEvent,
    CodexEventType,
    CodexHostManager,
    CodexSession,
    CodexSessionConfig,
)

_ENV_GATE = "CODEX_INTEGRATION"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get(_ENV_GATE) != "1",
        reason=f"set {_ENV_GATE}=1 to run the real Codex app-server smoke",
    ),
]


async def _wait_for(
    session: CodexSession,
    events: list[CodexEvent],
    expected: CodexEventType,
    *,
    timeout_s: float,
) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        events.extend(session.drain())
        if any(event.type is expected for event in events):
            return
        await asyncio.sleep(0.2)
    raise AssertionError(f"timed out waiting for {expected.value}")


def _git(workdir: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=workdir,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def test_real_diff_compact_and_review_commands() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="trowel-codex-commands-"))
    manager = CodexHostManager()
    try:
        _git(workdir, "init", "-q")
        _git(workdir, "config", "user.email", "integration@example.invalid")
        _git(workdir, "config", "user.name", "Trowel Integration")
        sample = workdir / "sample.txt"
        sample.write_text("alpha\n", encoding="utf-8")
        _git(workdir, "add", "sample.txt")
        _git(workdir, "commit", "-qm", "baseline")

        session = CodexSession(
            CodexSessionConfig(
                "native-commands",
                str(workdir),
                approval_policy="never",
                sandbox="workspace-write",
                ephemeral=True,
            )
        )
        manager.register(session)

        await manager.send(
            session,
            "Replace the exact word alpha in sample.txt with beta. Make no other changes.",
        )
        edit_events: list[CodexEvent] = []
        await _wait_for(
            session,
            edit_events,
            CodexEventType.FINISHED,
            timeout_s=180,
        )
        assert sample.read_text(encoding="utf-8") == "beta\n"
        diffs = [
            event
            for event in edit_events
            if event.type is CodexEventType.TURN_DIFF_UPDATED
        ]
        assert diffs
        assert "sample.txt" in diffs[-1].payload["diff"]

        await manager.compact(session)
        compact_events: list[CodexEvent] = []
        await _wait_for(
            session,
            compact_events,
            CodexEventType.COMPACTION,
            timeout_s=60,
        )

        await manager.start_review(session, {"type": "uncommittedChanges"})
        review_events: list[CodexEvent] = []
        await _wait_for(
            session,
            review_events,
            CodexEventType.FINISHED,
            timeout_s=180,
        )
        assert any(
            event.type is CodexEventType.REVIEW_MODE for event in review_events
        )
        assert not any(event.type is CodexEventType.USER for event in review_events)
    finally:
        await manager.close()
        shutil.rmtree(workdir, ignore_errors=True)
