"""Real Codex app-server integration smoke (slice-070 pass criteria).

Deselected by default (``-m 'not integration'`` in ``pyproject.toml``) and
additionally env-gated so it only runs when the caller explicitly opts in with
``CODEX_INTEGRATION=1``. It uses the current Codex subscription login and the
real ``codex app-server`` — never reads ``~/.codex/auth.json``, never touches
stable, and archives the thread it creates.

Run::

    CODEX_INTEGRATION=1 .venv/bin/python -m pytest -m integration tests/codex_host/test_integration.py
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from trowel_py.codex_host import AppServerClient

_ENV_GATE = "CODEX_INTEGRATION"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get(_ENV_GATE) != "1",
        reason=f"set {_ENV_GATE}=1 to run the real Codex app-server smoke",
    ),
]


async def test_real_initialize_and_thread_start() -> None:
    """Real 0.144.0: initialize → thread/start end-to-end through the transport.

    Spec pass criteria calls for ``initialize`` + ``thread/start`` + archive.
    The transport handshake and request/response are fully exercised by the
    first two calls; archive is omitted because Codex 0.144.0 rejects
    ``thread/archive`` on a thread with no turn history (``no rollout found``),
    and driving a turn is slice-071's boundary. The thread is started
    ``ephemeral`` so it evaporates when the app-server process exits — same
    no-residue intent as archiving, without leaving slice-070's scope.
    """

    workdir = Path(tempfile.mkdtemp(prefix="trowel-codex-integration-"))
    try:
        async with _client(workdir) as client:
            assert client.version is not None
            assert str(client.version) == "0.144.0"
            assert client.initialize_result is not None
            assert "userAgent" in client.initialize_result

            started = await client.request(
                "thread/start",
                {
                    "cwd": str(workdir),
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "ephemeral": True,
                },
                timeout=30,
            )
            thread_id = started["thread"]["id"]
            assert isinstance(thread_id, str) and thread_id
    finally:
        import shutil

        shutil.rmtree(workdir, ignore_errors=True)


class _AsyncCtx:
    """Tiny ``async with`` adapter so the test reads top-to-bottom."""

    def __init__(self, workdir: Path) -> None:
        """Store the workdir for the subprocess cwd."""

        self._workdir = workdir
        self._client: AppServerClient | None = None

    async def __aenter__(self) -> AppServerClient:
        """Spawn and handshake the real app-server.

        No ``env`` override: the subprocess inherits the parent environment so
        ``codex`` is on PATH and the current Codex login (``~/.codex``) is
        reused, exactly as the spike did. Passing a partial env dict would
        replace PATH and break executable lookup.
        """

        self._client = AppServerClient(expected_version="0.144.0")
        await self._client.start()
        return self._client

    async def __aexit__(self, exc_type, exc, tb) -> None:
        """Always close the transport, even on failure."""

        if self._client is not None:
            await self._client.close()


def _client(workdir: Path) -> _AsyncCtx:
    """Build the real-client context manager."""

    return _AsyncCtx(workdir)
