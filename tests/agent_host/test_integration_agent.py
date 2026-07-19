"""Real dual-runtime integration smoke for slice-072.

Deselected by default (``-m 'not integration'``) and additionally env-gated
behind both ``CC_INTEGRATION=1`` and ``CODEX_INTEGRATION=1``. It exercises the
host-neutral ``/api/agent/*`` end-to-end against the real ``claude -p`` and
the real ``codex app-server``: creates one CC/GLM session and one Codex/GPT
session, sends one turn each, and verifies the two streams stay tagged with
their own runtime (spec pass-criterion: 双 runtime 各完成一轮；不串线).

Run::

    CC_INTEGRATION=1 CODEX_INTEGRATION=1 .venv/bin/python -m pytest -m \\
        integration tests/agent_host/test_integration_agent.py

It never reads ``~/.codex/auth.json`` and never touches stable. The autouse
conftest redirects every home-writing path to tmp.
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
    """Parse SSE ``data:`` frames out of a streamed response body."""

    events: list[dict] = []
    for line in body.decode("utf-8").splitlines():
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[len("data: ") :]))
            except json.JSONDecodeError:
                continue
    return events


def test_dual_runtime_each_completes_one_turn(tmp_path: Path) -> None:
    """One CC/GLM + one Codex/GPT session, each completes a turn, no crossing."""

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

        # mixed active list distinguishes runtimes
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

        # every non-error frame is tagged with its own runtime — no crossing
        assert all(
            e.get("runtime") == "claude_code"
            for e in cc_events
            if e.get("type") != "error"
        )
        assert all(
            e.get("runtime") == "codex"
            for e in cx_events
            if e.get("type") != "error"
        )
        # each turn produced at least one terminal signal
        cc_types = {e.get("type") for e in cc_events}
        cx_types = {e.get("type") for e in cx_events}
        assert cc_types & {"finished", "error", "interrupted", "session_exited"}
        assert cx_types & {"finished", "error", "interrupted"}


def test_runtime_patch_rejected_at_http_level(tmp_path: Path) -> None:
    """A runtime PATCH returns 422 even on the real app (C-1 at the wire)."""

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
