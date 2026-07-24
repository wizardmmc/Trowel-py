"""使用真实 ``claude -p`` 与 Codex app-server 验证双 runtime 边界。

测试默认排除，并同时要求 ``CC_INTEGRATION=1`` 与 ``CODEX_INTEGRATION=1``。

运行：

    CC_INTEGRATION=1 CODEX_INTEGRATION=1 .venv/bin/python -m pytest -m \\
        integration tests/agent_host/routes/test_dual_runtime_integration.py

全局 fixture 将 Agent Host 的写入路径重定向到临时目录。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("CC_INTEGRATION") != "1"
        or os.environ.get("CODEX_INTEGRATION") != "1",
        reason=(
            "set CC_INTEGRATION=1 and CODEX_INTEGRATION=1 to run the real "
            "dual-runtime smoke"
        ),
    ),
]


def _sse_events(body: bytes) -> list[dict]:
    events: list[dict] = []
    for line in body.decode("utf-8").splitlines():
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[len("data: ") :]))
            except json.JSONDecodeError:
                continue
    return events


def test_dual_runtime_each_completes_one_turn(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from trowel_py.app import create_app

    workdir = tmp_path / "proj"
    workdir.mkdir()
    app = create_app()
    with TestClient(app) as client:
        cc = client.post(
            "/api/agent/sessions",
            json={"runtime": "claude_code", "workdir": str(workdir)},
        ).json()["data"]
        cx = client.post(
            "/api/agent/sessions",
            json={"runtime": "codex", "workdir": str(workdir)},
        ).json()["data"]

        active = client.get("/api/agent/sessions/active").json()["data"]
        runtimes = {s["runtime"] for s in active["sessions"]}
        assert runtimes == {"claude_code", "codex"}

        cc_resp = client.post(
            f"/api/agent/sessions/{cc['session_id']}/messages",
            json={"text": "reply with the single word: pong"},
        )
        cx_resp = client.post(
            f"/api/agent/sessions/{cx['session_id']}/messages",
            json={"text": "reply with the single word: pong"},
        )

        cc_events = _sse_events(cc_resp.content)
        cx_events = _sse_events(cx_resp.content)

        assert all(
            e.get("runtime") == "claude_code"
            for e in cc_events
            if e.get("type") != "error"
        )
        assert all(
            e.get("runtime") == "codex" for e in cx_events if e.get("type") != "error"
        )
        cc_types = {e.get("type") for e in cc_events}
        cx_types = {e.get("type") for e in cx_events}
        assert cc_types & {"finished", "error", "interrupted", "session_exited"}
        assert cx_types & {"finished", "error", "interrupted"}


def test_events_are_unified_agent_event_v1_envelopes(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from trowel_py.app import create_app
    from trowel_py.schemas.agent_host import AGENT_EVENT_SCHEMA

    workdir = tmp_path / "proj"
    workdir.mkdir()
    app = create_app()
    with TestClient(app) as client:
        cc = client.post(
            "/api/agent/sessions",
            json={"runtime": "claude_code", "workdir": str(workdir)},
        ).json()["data"]
        cx = client.post(
            "/api/agent/sessions",
            json={"runtime": "codex", "workdir": str(workdir)},
        ).json()["data"]

        cc_events = _sse_events(
            client.post(
                f"/api/agent/sessions/{cc['session_id']}/messages",
                json={"text": "reply with the single word: pong"},
            ).content
        )
        cx_events = _sse_events(
            client.post(
                f"/api/agent/sessions/{cx['session_id']}/messages",
                json={"text": "reply with the single word: pong"},
            ).content
        )

        for events, sid, runtime in (
            (cc_events, cc["session_id"], "claude_code"),
            (cx_events, cx["session_id"], "codex"),
        ):
            assert events, f"{runtime} stream produced no events"
            for ev in events:
                assert ev["schema"] == AGENT_EVENT_SCHEMA, ev
                assert ev["session_id"] == sid
                assert ev["runtime"] == runtime
                assert isinstance(ev["seq"], int) and ev["seq"] >= 1
                assert "type" in ev and "payload" in ev
            seqs = [ev["seq"] for ev in events]
            assert seqs == sorted(seqs), f"{runtime} seq not monotonic: {seqs}"
            assert len(seqs) == len(set(seqs)), f"{runtime} seq has dups: {seqs}"
            assert seqs[0] == 1, f"{runtime} seq does not start at 1: {seqs[0]}"

        codex_types = {ev["type"] for ev in cx_events}
        renamed = {
            "assistant_delta",
            "reasoning_delta",
            "tool_started",
            "tool_completed",
            "assistant_message",
        }
        assert codex_types.isdisjoint(renamed), (
            f"Codex stream still carries pre-unification types: {codex_types & renamed}"
        )
        assert "text" in codex_types or "finished" in codex_types, codex_types


def test_runtime_patch_rejected_at_http_level(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from trowel_py.app import create_app

    workdir = tmp_path / "proj"
    workdir.mkdir()
    app = create_app()
    with TestClient(app) as client:
        cc = client.post(
            "/api/agent/sessions",
            json={"runtime": "claude_code", "workdir": str(workdir)},
        ).json()["data"]
        resp = client.patch(
            f"/api/agent/sessions/{cc['session_id']}", json={"runtime": "codex"}
        )
        assert resp.status_code == 422
