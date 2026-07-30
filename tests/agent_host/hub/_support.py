from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from trowel_py.agent_host.schemas import CreateAgentSessionRequest
from trowel_py.cc_host.routes import OpenedCcSession
from trowel_py.codex_host.commands import command_roster
from trowel_py.schemas.agent_host import AGENT_EVENT_SCHEMA


class FakeCcHost:
    def __init__(
        self,
        workdir: str,
        model: str = "glm-5.2",
        effort: str | None = None,
        permission_mode: str = "bypassPermissions",
    ) -> None:
        self.workdir = workdir
        self.model = model
        self.effort = effort
        self.permission_mode = permission_mode
        self.running = False
        self.is_dead = False
        self.memory_enabled = True
        self.profile_enabled = True
        self.session_kind = "user"
        self.agent_mcp_enabled = True
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
        self.threads: list[dict[str, Any]] = []
        self.thread_reads: dict[str, dict[str, Any]] = {}
        self.list_thread_calls: list[tuple[str, int]] = []
        self.read_thread_calls: list[str] = []
        self.attached: list[str] = []
        self.goals: dict[str, dict[str, Any] | None] = {}
        self.goal_sets: list[dict[str, Any]] = []
        self.goal_clears: list[str] = []
        self.compactions: list[str] = []
        self.reviews: list[dict[str, Any]] = []
        self.attach_results: dict[str, dict[str, Any]] = {}
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
        manages_turn_state = hasattr(session, "begin_send")
        if manages_turn_state:
            session.begin_send()
        if getattr(session, "binding", None) is None and hasattr(
            session, "attach_thread_binding"
        ):
            session.attach_thread_binding(
                {
                    "thread": {"id": "thread-1"},
                    "model": "gpt-5.6-sol",
                    "modelProvider": "openai",
                    "cwd": session.config.workdir,
                    "sandbox": {"mode": "read-only"},
                    "approvalPolicy": "never",
                }
            )
        if before_turn_start is not None:
            before_turn_start(session)
        for event in reversed(getattr(session, "_events", ())):
            turn_id = getattr(event, "turn_id", None)
            if isinstance(turn_id, str):
                if manages_turn_state:
                    session.record_turn_started(turn_id, text)
                return turn_id
        if manages_turn_state:
            session.record_turn_started("fake-turn-id", text)
        return "fake-turn-id"

    async def interrupt(self, session: Any) -> None:
        self.interrupted.append(session.session_id)

    async def list_models(self) -> list[dict[str, Any]]:

        return self.models

    async def list_commands(self) -> list[dict[str, Any]]:
        return command_roster("0.144.0")

    async def list_threads(
        self,
        *,
        cwd: str,
        limit: int,
        excluded_ids: frozenset[str] = frozenset(),
    ) -> list[dict[str, Any]]:
        self.list_thread_calls.append((cwd, limit))
        return [
            thread for thread in self.threads if thread.get("id") not in excluded_ids
        ][:limit]

    async def read_thread(self, thread_id: str) -> dict[str, Any]:
        self.read_thread_calls.append(thread_id)
        return self.thread_reads[thread_id]

    async def attach(self, session: Any) -> Any:
        thread_id = session.config.initial_thread_id
        self.attached.append(session.session_id)
        result = self.attach_results.get(thread_id)
        if result is None:
            result = {
                "thread": {"id": thread_id or "thread-1"},
                "model": "gpt-5.6-sol",
                "modelProvider": "openai",
                "cwd": session.config.workdir,
                "sandbox": {"mode": "read-only"},
                "approvalPolicy": "never",
            }
        return session.attach_thread_binding(result)

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

    async def get_goal(self, session: Any) -> dict[str, Any] | None:
        return self.goals.get(session.session_id)

    async def set_goal(self, session: Any, **fields: Any) -> dict[str, Any]:
        self.goal_sets.append({"session_id": session.session_id, **fields})
        binding = getattr(session, "binding", None)
        goal = {
            "threadId": getattr(binding, "thread_id", "thread-1"),
            "objective": fields.get("objective") or "Existing objective",
            "status": fields.get("status") or "active",
            "tokenBudget": fields.get("token_budget"),
            "tokensUsed": 0,
            "timeUsedSeconds": 0,
            "createdAt": 10,
            "updatedAt": 11,
        }
        self.goals[session.session_id] = goal
        return goal

    async def clear_goal(self, session: Any) -> bool:
        self.goal_clears.append(session.session_id)
        self.goals[session.session_id] = None
        return True

    async def compact(self, session: Any, *, before_start=None) -> None:
        await self.attach(session)
        if before_start is not None:
            before_start(session)
        self.compactions.append(session.session_id)

    async def start_review(
        self, session: Any, target: dict[str, Any], *, before_start=None
    ) -> dict[str, str]:
        binding = await self.attach(session)
        if before_start is not None:
            before_start(session)
        self.reviews.append({"session_id": session.session_id, "target": target})
        return {
            "review_thread_id": binding.thread_id,
            "turn_id": "review-turn-1",
        }


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
        host = FakeCcHost(
            req.workdir,
            model=req.model or "glm-5.2",
            effort=req.effort,
            permission_mode=req.permission_mode or "bypassPermissions",
        )
        host.session_kind = req.session_kind
        host.agent_mcp_enabled = req.agent_mcp_enabled
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
