from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from trowel_py.agent_host.schemas import CreateAgentSessionRequest
from trowel_py.cc_host.routes import OpenedCcSession
from trowel_py.schemas.agent_host import AGENT_EVENT_SCHEMA


class FakeCcHost:
    def __init__(self, workdir: str, model: str = "glm-5.2") -> None:
        self.workdir = workdir
        self.model = model
        self.running = False
        self.is_dead = False
        self.memory_enabled = True
        self.profile_enabled = True
        self.closed = False
        self.interrupted = False
        self.cc_session_id: str | None = None

    async def send(self, text: str) -> AsyncIterator[dict[str, Any]]:
        self.running = True
        yield {"type": "text", "text": f"echo:{text}"}
        self.running = False

    async def interrupt(self) -> None:
        self.interrupted = True

    async def close(self) -> None:
        self.closed = True


class FakeCodexManager:
    def __init__(self) -> None:
        self.sessions: dict[str, Any] = {}
        self.sent: list[tuple[str, str]] = []
        self.interrupted: list[str] = []
        self.answered_requests: list[tuple[str, str, str]] = []
        self.models: list[dict[str, Any]] = [
            {
                "id": "gpt-5.6-sol",
                "model": "gpt-5.6-sol",
                "display_name": "Sol",
                "description": "",
                "is_default": True,
                "default_effort": "low",
                "supported_efforts": [
                    {"value": "low", "description": ""},
                    {"value": "ultra", "description": ""},
                ],
            },
            {
                "id": "gpt-5.6-luna",
                "model": "gpt-5.6-luna",
                "display_name": "Luna",
                "description": "",
                "is_default": False,
                "default_effort": "medium",
                "supported_efforts": [
                    {"value": "low", "description": ""},
                    {"value": "medium", "description": ""},
                ],
            },
        ]

    def register(self, session: Any) -> None:
        self.sessions[session.session_id] = session

    def get_session(self, sid: str) -> Any | None:
        return self.sessions.get(sid)

    @property
    def session_ids(self) -> tuple[str, ...]:

        return tuple(self.sessions.keys())

    def unregister(self, sid: str) -> Any | None:

        return self.sessions.pop(sid, None)

    async def send(
        self,
        session: Any,
        text: str,
        *,
        before_turn_start=None,
    ) -> str:

        self.sent.append((session.session_id, text))
        if before_turn_start is not None:
            before_turn_start(session)
        return "fake-turn-id"

    async def interrupt(self, session: Any) -> None:
        self.interrupted.append(session.session_id)

    async def list_models(self) -> list[dict[str, Any]]:

        return self.models

    def answer_request(self, session_id: str, request_id: str, decision: str) -> Any:

        self.answered_requests.append((session_id, request_id, decision))

        class _Answered:
            @staticmethod
            def to_payload() -> dict[str, Any]:

                return {
                    "request_id": request_id,
                    "status": "answered",
                    "decision": decision,
                }

        return _Answered()

    def list_requests(self, session_id: str) -> list[Any]:

        return []


class _FakeThreadBinding:
    def __init__(self, thread_id: str, model: str) -> None:
        self.thread_id = thread_id
        self.model = model


class FakeCodexSession:
    def __init__(
        self,
        session_id: str,
        events: list[Any],
        *,
        thread_id: str = "thr-1",
        model: str = "gpt-5.6-sol",
    ) -> None:
        self.session_id = session_id
        self._events = list(events)
        self.binding = _FakeThreadBinding(thread_id, model)
        self.state = "idle"

    async def events(self) -> AsyncIterator[Any]:
        for ev in self._events:
            yield ev


def make_cc_opener(registry: dict[str, FakeCcHost], name_counts: dict[str, int]):

    def opener(
        req: CreateAgentSessionRequest,
        reg: dict[str, Any] | None = None,
        *,
        proxy_base_url: str | None = None,
        settings_path: str | Path | None = None,
    ) -> OpenedCcSession:
        del proxy_base_url, settings_path
        sid = "cc-" + uuid.uuid4().hex[:8]
        host = FakeCcHost(req.workdir, model=req.model or "glm-5.2")
        target = reg if reg is not None else registry
        target[sid] = host
        registry[sid] = host
        basename = Path(req.workdir).name or str(req.workdir)
        n = name_counts.get(basename, 0)
        name_counts[basename] = n + 1
        name = basename if n == 0 else f"{basename} #{n + 1}"
        return OpenedCcSession(sid=sid, host=host, name=name)

    return opener


def cc_req(workdir: Path, **over: Any) -> CreateAgentSessionRequest:
    base: dict[str, Any] = dict(runtime="claude_code", workdir=str(workdir))
    base.update(over)
    return CreateAgentSessionRequest(**base)


def codex_req(workdir: Path, **over: Any) -> CreateAgentSessionRequest:
    base: dict[str, Any] = dict(runtime="codex", workdir=str(workdir))
    base.update(over)
    return CreateAgentSessionRequest(**base)


def _is_envelope(e: dict) -> bool:

    return e.get("schema") == AGENT_EVENT_SCHEMA
