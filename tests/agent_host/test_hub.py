"""slice-072: SessionHub — host-neutral create / route / freeze invariants.

The Hub owns the :class:`BindingStore` (persistence) and routes create / send
/ interrupt / resume to the right native host (CCHost via cc_host's registry,
or the shared CodexHostManager). Tests inject fakes for both sides so the
routing logic is exercised deterministically without spawning any real
subprocess (spec C-7).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, AsyncIterator

import pytest
from fastapi import HTTPException

from trowel_py.agent_host.binding import Runtime, make_binding
from trowel_py.agent_host.hub import (
    CrossRuntimeResumeError,
    RuntimeFrozenError,
    SessionHub,
)
from trowel_py.agent_host.schemas import CreateAgentSessionRequest
from trowel_py.agent_host.store import BindingStore
from trowel_py.cc_host.routes import OpenedCcSession


# ---------------------------------------------------------------------------
# Fakes — duck-typed stands for CCHost and the CodexHostManager.
# ---------------------------------------------------------------------------


class FakeCcHost:
    """Minimal CCHost stand-in: the Hub only reads a few attrs + send/close."""

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
    """Duck-typed stand-in for the CodexHostManager surface the Hub uses."""

    def __init__(self) -> None:
        self.sessions: dict[str, Any] = {}
        self.sent: list[tuple[str, str]] = []
        self.interrupted: list[str] = []

    def register(self, session: Any) -> None:
        self.sessions[session.session_id] = session

    def get_session(self, sid: str) -> Any | None:
        return self.sessions.get(sid)

    @property
    def session_ids(self) -> tuple[str, ...]:
        """Mirror CodexHostManager.session_ids (review CRITICAL-1 alignment)."""

        return tuple(self.sessions.keys())

    def unregister(self, sid: str) -> Any | None:
        """Mirror CodexHostManager.unregister."""

        return self.sessions.pop(sid, None)

    async def send(self, session: Any, text: str) -> str:
        self.sent.append((session.session_id, text))
        return "fake-turn-id"

    async def interrupt(self, session: Any) -> None:
        self.interrupted.append(session.session_id)


def make_cc_opener(
    registry: dict[str, FakeCcHost], name_counts: dict[str, int]
):
    """Build a cc_opener that constructs a FakeCcHost, not a real CCHost."""

    def opener(
        req: CreateAgentSessionRequest, request: Any, reg: dict[str, Any] | None = None
    ) -> OpenedCcSession:
        del request  # the fake does not read app.state
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cc_registry() -> dict[str, FakeCcHost]:
    return {}


@pytest.fixture
def name_counts() -> dict[str, int]:
    return {}


@pytest.fixture
def codex_mgr() -> FakeCodexManager:
    return FakeCodexManager()


@pytest.fixture
def hub(
    tmp_path: Path,
    cc_registry: dict[str, FakeCcHost],
    name_counts: dict[str, int],
    codex_mgr: FakeCodexManager,
) -> SessionHub:
    store = BindingStore(tmp_path / "agent_sessions.json")
    return SessionHub(
        store,
        codex_manager=codex_mgr,
        cc_registry=cc_registry,
        cc_opener=make_cc_opener(cc_registry, name_counts),
    )


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    d = tmp_path / "proj"
    d.mkdir()
    return d


def cc_req(workdir: Path, **over: Any) -> CreateAgentSessionRequest:
    base: dict[str, Any] = dict(runtime="claude_code", workdir=str(workdir))
    base.update(over)
    return CreateAgentSessionRequest(**base)


def codex_req(workdir: Path, **over: Any) -> CreateAgentSessionRequest:
    base: dict[str, Any] = dict(runtime="codex", workdir=str(workdir))
    base.update(over)
    return CreateAgentSessionRequest(**base)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_cc_session_creates_binding_and_registry_host(
    hub: SessionHub, workdir: Path, cc_registry: dict[str, FakeCcHost]
):
    binding = hub.create(cc_req(workdir), request=None)
    assert binding.runtime is Runtime.CLAUDE_CODE
    assert binding.workdir == str(workdir)
    assert binding.session_id in cc_registry
    assert cc_registry[binding.session_id].workdir == str(workdir)
    assert hub.get(binding.session_id) is not None


def test_create_codex_session_creates_binding_and_registers_manager(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):
    binding = hub.create(codex_req(workdir), request=None)
    assert binding.runtime is Runtime.CODEX
    assert binding.session_id in codex_mgr.sessions
    assert hub.get(binding.session_id) is not None


def test_create_cc_with_resume_carries_native_id(hub: SessionHub, workdir: Path):
    binding = hub.create(cc_req(workdir, resume_from="cc-jsonl-9"), request=None)
    assert binding.native_session_id == "cc-jsonl-9"


def test_create_codex_with_resume_seeds_thread_binding(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):
    """resume_from seeds the Codex session so the first send resumes (HIGH-4).

    Without the seed, CodexSession.is_new_thread stays True and manager.send
    would call thread/start (a new conversation) instead of thread/resume.
    """

    binding = hub.create(
        codex_req(workdir, resume_from="codex-thread-abc"), request=None
    )
    session = codex_mgr.get_session(binding.session_id)
    assert session is not None
    assert session.config.initial_thread_id == "codex-thread-abc"
    assert session.binding is not None
    assert session.binding.thread_id == "codex-thread-abc"
    assert session.is_new_thread is False


async def test_delete_codex_unregisters_from_manager(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):
    """delete() routes through manager.unregister, not a private attr poke."""

    binding = hub.create(codex_req(workdir), request=None)
    sid = binding.session_id
    assert sid in codex_mgr.sessions
    assert await hub.delete(sid) is True
    assert sid not in codex_mgr.sessions


async def test_delete_cc_clears_cc_multiopen_state(
    hub: SessionHub, workdir: Path, monkeypatch: pytest.MonkeyPatch
):
    """delete() on a CC session clears cc_host multi-open bookkeeping (HIGH-1).

    The fake cc_opener does not touch cc_host's module globals (only the real
    open_cc_session does), so we isolate those globals with monkeypatch and
    pre-seed them to mirror the real opener's side effects, then assert the
    closer clears them.
    """

    from trowel_py.cc_host import routes as cc_routes

    monkeypatch.setattr(cc_routes, "_WORKDIR_INDEX", {})
    monkeypatch.setattr(cc_routes, "_SESSION_NAMES", {})
    binding = hub.create(cc_req(workdir), request=None)
    sid = binding.session_id
    cc_routes._WORKDIR_INDEX.setdefault(str(workdir), set()).add(sid)
    cc_routes._SESSION_NAMES[sid] = "proj"
    await hub.delete(sid)
    assert sid not in cc_routes._SESSION_NAMES
    assert sid not in cc_routes._WORKDIR_INDEX.get(str(workdir), set())


def test_activate_cc_mirrors_legacy_active_sid(
    hub: SessionHub, workdir: Path, monkeypatch: pytest.MonkeyPatch
):
    """Activating a CC session mirrors cc_host._ACTIVE_SID (HIGH-3)."""

    from trowel_py.cc_host import routes as cc_routes

    monkeypatch.setattr(cc_routes, "_ACTIVE_SID", None)
    cc = hub.create(cc_req(workdir), request=None)
    cx = hub.create(codex_req(workdir), request=None)
    hub.activate(cc.session_id)
    assert cc_routes._ACTIVE_SID == cc.session_id
    # switching to the Codex session does not touch the legacy CC active id
    hub.activate(cx.session_id)
    assert cc_routes._ACTIVE_SID == cc.session_id


def test_create_missing_workdir_400(hub: SessionHub):
    with pytest.raises(HTTPException) as exc:
        hub.create(cc_req(Path("/nonexistent/xyz-123")), request=None)
    assert exc.value.status_code == 400


def test_create_connection_cap_409(hub: SessionHub, workdir: Path, monkeypatch):
    monkeypatch.setattr("trowel_py.agent_host.hub.MAX_CONNECTIONS", 1)
    hub.create(cc_req(workdir), request=None)
    with pytest.raises(HTTPException) as exc:
        hub.create(codex_req(workdir), request=None)
    assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# list_active (mixed)
# ---------------------------------------------------------------------------


def test_list_active_mixes_cc_and_codex(hub: SessionHub, workdir: Path):
    cc = hub.create(cc_req(workdir), request=None)
    cx = hub.create(codex_req(workdir), request=None)
    sessions, active_id = hub.list_active()
    ids = {s["session_id"] for s in sessions}
    runtimes = {s["runtime"] for s in sessions}
    assert ids == {cc.session_id, cx.session_id}
    assert runtimes == {"claude_code", "codex"}
    assert active_id == cx.session_id  # last-created is active


# ---------------------------------------------------------------------------
# runtime frozen (C-1) + cross-resume forbidden (C-2)
# ---------------------------------------------------------------------------


def test_patch_runtime_change_rejected(hub: SessionHub, workdir: Path):
    binding = hub.create(cc_req(workdir), request=None)
    with pytest.raises(RuntimeFrozenError):
        hub.patch(binding.session_id, runtime="codex")


def test_patch_same_runtime_ok(hub: SessionHub, workdir: Path):
    binding = hub.create(cc_req(workdir), request=None)
    hub.patch(binding.session_id, runtime="claude_code")  # no error


def test_validate_resume_rejects_cross_runtime(hub: SessionHub, workdir: Path):
    """A native id already bound to CC cannot be resumed as Codex (C-2)."""

    hub._store.put(
        make_binding(
            session_id="old-cc",
            runtime=Runtime.CLAUDE_CODE,
            native_session_id="cc-jsonl-1",
            workdir=str(workdir),
            model=None,
            effort=None,
            permission=None,
            memory_enabled=True,
            profile_enabled=True,
            capabilities=("tools",),
            name="proj",
        )
    )
    with pytest.raises(CrossRuntimeResumeError):
        hub.validate_resume(Runtime.CODEX, "cc-jsonl-1")


def test_validate_resume_same_runtime_ok(hub: SessionHub):
    hub.validate_resume(Runtime.CLAUDE_CODE, "fresh-cc-id")  # no conflict
    hub.validate_resume(Runtime.CODEX, None)  # fresh codex thread


# ---------------------------------------------------------------------------
# activate / delete / restart
# ---------------------------------------------------------------------------


def test_activate_sets_active_id(hub: SessionHub, workdir: Path):
    cc = hub.create(cc_req(workdir), request=None)
    cx = hub.create(codex_req(workdir), request=None)
    hub.activate(cc.session_id)
    sessions, active_id = hub.list_active()
    assert active_id == cc.session_id
    # C-4: switching the active view does not drop the other session.
    assert cx.session_id in {s["session_id"] for s in sessions}


async def test_delete_cc_closes_host_and_drops_binding(
    hub: SessionHub, workdir: Path, cc_registry: dict[str, FakeCcHost]
):
    binding = hub.create(cc_req(workdir), request=None)
    sid = binding.session_id
    host = cc_registry[sid]
    assert await hub.delete(sid) is True
    assert hub.get(sid) is None
    assert sid not in cc_registry
    assert host.closed is True


async def test_delete_codex_drops_binding(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):
    binding = hub.create(codex_req(workdir), request=None)
    sid = binding.session_id
    assert await hub.delete(sid) is True
    assert hub.get(sid) is None
    assert sid not in codex_mgr.sessions


async def test_delete_unknown_returns_false(hub: SessionHub):
    assert await hub.delete("nope") is False


def test_restart_recovers_bindings_from_store(
    hub: SessionHub, workdir: Path, tmp_path: Path
):
    cc = hub.create(cc_req(workdir), request=None)
    cx = hub.create(codex_req(workdir), request=None)
    # simulate a trowel restart: brand-new Hub on the same store file
    restarted = SessionHub(
        BindingStore(hub._store.path),
        codex_manager=FakeCodexManager(),
        cc_registry={},
        cc_opener=make_cc_opener({}, {}),
    )
    bindings = {b.session_id: b for b in restarted._store.list_all()}
    assert cc.session_id in bindings
    assert cx.session_id in bindings
    assert bindings[cc.session_id].runtime is Runtime.CLAUDE_CODE
    assert bindings[cx.session_id].runtime is Runtime.CODEX


# ---------------------------------------------------------------------------
# interrupt routing
# ---------------------------------------------------------------------------


async def test_interrupt_routes_to_cc(
    hub: SessionHub, workdir: Path, cc_registry: dict[str, FakeCcHost]
):
    binding = hub.create(cc_req(workdir), request=None)
    await hub.interrupt(binding.session_id)
    assert cc_registry[binding.session_id].interrupted is True


async def test_interrupt_routes_to_codex(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):
    binding = hub.create(codex_req(workdir), request=None)
    await hub.interrupt(binding.session_id)
    assert binding.session_id in codex_mgr.interrupted


async def test_interrupt_unknown_session_404(hub: SessionHub):
    with pytest.raises(HTTPException) as exc:
        await hub.interrupt("nope")
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# CC stream (Codex stream is exercised by the integration smoke)
# ---------------------------------------------------------------------------


async def test_stream_cc_yields_events_with_runtime_tag(hub: SessionHub, workdir: Path):
    binding = hub.create(cc_req(workdir), request=None)
    events = [e async for e in hub.stream(binding.session_id, "hello")]
    assert events, "expected at least one event from the CC stream"
    assert all(e.get("runtime") == "claude_code" for e in events)


async def test_stream_unknown_session_404(hub: SessionHub):
    with pytest.raises(HTTPException) as exc:
        _ = [e async for e in hub.stream("nope", "hi")]
    assert exc.value.status_code == 404
