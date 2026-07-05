"""Tests for cc_host.service.CCHost using a fake CC subprocess.

No real `claude` is spawned. A fake process feeds preset stdout lines so we
can assert the translated event stream, the respawn-after-interrupt path, and
the stalled auto-restart path.
"""
import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from trowel_py.cc_host import checkpoint
from trowel_py.cc_host.service import CCHost
from trowel_py.cc_host.session_scan import workdir_to_slug
from trowel_py.schemas.cc_host import (
    SessionStartedEvent,
    TextEvent,
    ToolCallEvent,
    FinishedEvent,
    ErrorEvent,
    LocalCommandEvent,
    StalledEvent,
    TurnStartEvent,
)


# ---------------------------------------------------------------------------
# Fake subprocess + spawner (test-only infrastructure)
# ---------------------------------------------------------------------------


class FakeWriter:
    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def write_eof(self) -> None:
        return None


class FakeProc:
    """Mimics the asyncio subprocess surface CCHost touches."""

    def __init__(self, lines: list[str], feed_eof: bool = True, pid: int = 1) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.stdin = FakeWriter()
        self.stdout = asyncio.StreamReader()
        for ln in lines:
            self.stdout.feed_data(ln.encode() + b"\n")
        if feed_eof:
            self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int | None:
        return self.returncode


class FakeSpawner:
    """Hands out FakeProc instances in order, recording spawn args."""

    def __init__(self, procs: list[FakeProc]) -> None:
        self._procs = list(procs)
        self.spawned: list[tuple[list[str], dict]] = []

    async def __call__(self, args: list[str], kwargs: dict) -> FakeProc:
        proc = self._procs.pop(0)
        self.spawned.append((args, kwargs))
        return proc


# end fakes
# ---------------------------------------------------------------------------


def line(d: dict) -> str:
    return json.dumps(d)


def init_event(sid="s-1", model="glm-5.2"):
    return {"type": "system", "subtype": "init", "model": model,
            "cwd": "/wd", "session_id": sid, " tools": [], "tools": ["Read", "Skill"]}


def text_delta(i, t):
    return {"type": "stream_event", "event": {"type": "content_block_delta",
            "index": i, "delta": {"type": "text_delta", "text": t}}}


def result_ok():
    return {"type": "result", "subtype": "success", "is_error": False,
            "total_cost_usd": 0.03, "usage": {"input_tokens": 5}, "num_turns": 1}


async def collect(gen):
    return [x async for x in gen]


class TestStartAndSend:
    async def test_send_yields_session_started_text_finished(self, tmp_path: Path):
        proc = FakeProc([
            line(init_event()),
            line(text_delta(0, "hel")),
            line(text_delta(0, "lo")),
            line(result_ok()),
        ])
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("hi"))
        kinds = [type(e).__name__ for e in events]
        # slice-026: a TurnStartEvent is yielded first (before SessionStarted)
        assert kinds[0] == "TurnStartEvent"
        assert SessionStartedEvent in [type(e) for e in events]
        text_ev = [e for e in events if isinstance(e, TextEvent)]
        assert "".join(e.text for e in text_ev) == "hello"
        assert isinstance(events[-1], FinishedEvent)
        assert host.cc_session_id == "s-1"

    async def test_send_writes_user_message_to_stdin(self, tmp_path: Path):
        proc = FakeProc([line(init_event()), line(result_ok())])
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        await collect(host.send("ping"))
        written = b"".join(proc.stdin.written).decode()
        assert '"type": "user"' in written
        assert "ping" in written

    async def test_tool_call_streamed(self, tmp_path: Path):
        proc = FakeProc([
            line(init_event()),
            line({"type": "stream_event", "event": {"type": "content_block_start",
                "index": 0, "content_block": {"type": "tool_use", "id": "tu_1", "name": "Write"}}}),
            line({"type": "stream_event", "event": {"type": "content_block_delta",
                "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"p":"/a"}'}}}),
            line({"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}}),
            line(result_ok()),
        ])
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("write a file"))
        calls = [e for e in events if isinstance(e, ToolCallEvent)]
        assert len(calls) == 1
        assert calls[0].tool_name == "Write"


class TestLocalAndRestart:
    async def test_cost_does_not_hit_cc(self, tmp_path: Path):
        proc = FakeProc([line(init_event()), line(result_ok())])
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        await collect(host.send("hi"))  # accumulate cost
        proc.stdin.written.clear()
        events = await collect(host.send("/cost"))
        assert any(isinstance(e, LocalCommandEvent) for e in events)
        # nothing written to CC for a local command
        assert proc.stdin.written == []

    async def test_effort_triggers_restart_next_send(self, tmp_path: Path):
        proc1 = FakeProc([line(init_event()), line(result_ok())])
        proc2 = FakeProc([line(init_event(sid="s-1")), line(result_ok())])
        spawner = FakeSpawner([proc1, proc2])
        host = CCHost("sid", tmp_path, spawner=spawner)
        await collect(host.send("hi"))
        await collect(host.send("/effort high"))
        assert host.effort == "high"
        # next real send respawns because interrupt/restart killed the process
        await collect(host.send("again"))
        # second proc was launched resuming the same cc session id
        assert len(spawner.spawned) >= 2
        second_args = spawner.spawned[1][0]
        assert "--resume" in second_args
        assert "s-1" in second_args

    async def test_model_emits_model_changed_for_immediate_sync(
        self, tmp_path: Path
    ):
        """slice-027 C2: /model emits ModelChangedEvent so the StatusBar syncs
        immediately, not on the next message (CC is lazy-restarted by the next
        send's _ensure_process)."""
        proc1 = FakeProc([line(init_event()), line(result_ok())])
        spawner = FakeSpawner([proc1])
        host = CCHost("sid", tmp_path, spawner=spawner)
        await collect(host.send("hi"))
        events = await collect(host.send("/model opus"))
        assert host.model == "opus"
        types = [e.type for e in events]
        assert "model_changed" in types
        mc = next(e for e in events if e.type == "model_changed")
        assert mc.model == "opus"

    async def test_effort_emits_model_changed_with_effort(
        self, tmp_path: Path
    ):
        proc1 = FakeProc([line(init_event()), line(result_ok())])
        spawner = FakeSpawner([proc1])
        host = CCHost("sid", tmp_path, spawner=spawner)
        await collect(host.send("hi"))
        events = await collect(host.send("/effort high"))
        mc = next(e for e in events if e.type == "model_changed")
        assert mc.effort == "high"

    async def test_bare_model_does_not_kill_process(self, tmp_path: Path):
        """slice-027: bare /model (no arg) must NOT kill the live CC process —
        the frontend picker intercepts it; if it reaches the backend anyway,
        we emit a usage hint and leave the process alone (no kill+respawn)."""
        proc1 = FakeProc([line(init_event()), line(result_ok())])
        spawner = FakeSpawner([proc1])
        host = CCHost("sid", tmp_path, spawner=spawner)
        await collect(host.send("hi"))
        events = await collect(host.send("/model"))
        # only the first send spawned — /model no-arg did not kill+respawn
        assert len(spawner.spawned) == 1
        # emits a usage local_command, NOT a model_changed
        assert "model_changed" not in [e.type for e in events]
        assert any(e.type == "local_command" for e in events)

    async def test_model_picked_args_reach_next_spawn(self, tmp_path: Path):
        """slice-027: after /model opus, the NEXT send's _ensure_process
        spawns CC with --model opus (lazy restart applies the new model)."""
        proc1 = FakeProc([line(init_event()), line(result_ok())])
        proc2 = FakeProc([line(init_event(sid="s-1")), line(result_ok())])
        spawner = FakeSpawner([proc1, proc2])
        host = CCHost("sid", tmp_path, spawner=spawner)
        await collect(host.send("hi"))
        await collect(host.send("/model opus"))
        await collect(host.send("again"))
        assert len(spawner.spawned) >= 2
        second_args = spawner.spawned[1][0]
        i = second_args.index("--model")
        assert second_args[i + 1] == "opus"


class TestInterrupt:
    async def test_interrupt_marks_dead_and_next_send_resumes(self, tmp_path: Path):
        proc1 = FakeProc([line(init_event()), line(result_ok())])
        proc2 = FakeProc([line(init_event(sid="s-1")), line(result_ok())])
        spawner = FakeSpawner([proc1, proc2])
        host = CCHost("sid", tmp_path, spawner=spawner)
        # fake proc has no real process group; emulate SIGINT by marking dead
        host._interrupt_proc = lambda p: setattr(p, "returncode", 0)
        await collect(host.send("hi"))
        await host.interrupt()
        # process is dead now
        assert host.is_dead is True
        await collect(host.send("next"))
        assert len(spawner.spawned) == 2
        second_args = spawner.spawned[1][0]
        assert "--resume" in second_args and "s-1" in second_args


class TestStalled:
    async def test_stalled_triggers_one_restart_then_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        # First proc: init ok, then goes silent (no more lines, no EOF).
        proc1 = FakeProc([line(init_event())], feed_eof=False)
        proc2 = FakeProc([line(init_event(sid="s-1"))], feed_eof=False)
        spawner = FakeSpawner([proc1, proc2])
        # virtual clock so we don't actually sleep 100s
        clock = {"t": 0.0}
        def fake_now():
            clock["t"] += 200.0  # jump past threshold every tick
            return clock["t"]
        host = CCHost(
            "sid", tmp_path, spawner=spawner,
            now=fake_now, stalled_threshold=100.0, stalled_tick=0.001,
        )
        events = await collect(host.send("long job"))
        kinds = [type(e).__name__ for e in events]
        # first stall -> respawn (proc2), second stall -> error
        assert "ErrorEvent" in kinds
        assert len(spawner.spawned) == 2


class TestCancellation:
    async def test_aborted_send_kills_subprocess_no_orphan(self, tmp_path: Path):
        # proc that blocks forever on stdout (no data, no EOF)
        proc = FakeProc([], feed_eof=False)
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]),
                      stalled_threshold=10000.0, stalled_tick=0.01)

        async def _run():
            async for _ in host.send("hi"):
                pass

        task = asyncio.create_task(_run())
        await asyncio.sleep(0.05)  # let it enter the readline wait
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # the process was force-killed on cancellation (no orphan)
        assert proc.returncode is not None


class TestElicitAnswer:
    """answer_elicit / cancel_elicit write control_response to stdin (slice-025-c).

    Ground truth: reverse_cc samples/raw/052_askuser_bypass_stdio.jsonl —
    AskUserQuestion triggers control_request(can_use_tool); the host must reply
    with control_response(behavior=allow, updatedInput={questions, answers,
    annotations}). answers is a required record (052 ZodError when absent).
    """

    async def test_answer_elicit_writes_allow_with_answers(self, tmp_path: Path):
        proc = FakeProc([])
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        host._proc = proc
        host._pending_elicit = {
            "request_id": "req-1",
            "tool_use_id": "call_abc",
            "questions": [{"question": "A or B?", "header": "Pref",
                           "options": [{"label": "A"}], "multiSelect": False}],
        }
        ok = await host.answer_elicit({"A or B?": "A"})
        assert ok is True
        assert host._pending_elicit is None
        payload = json.loads(proc.stdin.written[-1].decode())
        assert payload["type"] == "control_response"
        assert payload["response"]["subtype"] == "success"
        assert payload["response"]["request_id"] == "req-1"
        assert payload["response"]["response"]["behavior"] == "allow"
        updated = payload["response"]["response"]["updatedInput"]
        assert updated["answers"] == {"A or B?": "A"}
        assert updated["questions"][0]["header"] == "Pref"
        assert updated["annotations"] == {}

    async def test_cancel_elicit_writes_deny(self, tmp_path: Path):
        proc = FakeProc([])
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        host._proc = proc
        host._pending_elicit = {"request_id": "req-2", "tool_use_id": "c",
                                "questions": []}
        ok = await host.cancel_elicit()
        assert ok is True
        assert host._pending_elicit is None
        payload = json.loads(proc.stdin.written[-1].decode())
        assert payload["response"]["response"]["behavior"] == "deny"
        assert "declined" in payload["response"]["response"]["message"]

    async def test_answer_without_pending_returns_false(self, tmp_path: Path):
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([FakeProc([])]))
        ok = await host.answer_elicit({"q": "a"})
        assert ok is False


# ---------------------------------------------------------------------------
# slice-026 E1: checkpoint integration on send()
# ---------------------------------------------------------------------------


def _git_repo(path: Path) -> Path:
    """Init a tmp git repo with one commit; return its root."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "seed").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)
    return path


class TestCheckpointOnSend:
    async def test_non_git_workdir_yields_non_revertible_turn_start(self, tmp_path):
        proc = FakeProc([line(init_event()), line(result_ok())])
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        events = await collect(host.send("hi"))
        assert isinstance(events[0], TurnStartEvent)
        assert events[0].revertible is False
        assert events[0].turn_id

    async def test_fresh_turn_1_revertible_via_session_start_checkpoint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # slice-026: fresh session turn 1 IS revertible — the session-start
        # checkpoint is saved in the init handler once cc_session_id is known
        # (worktree is still pristine at init).
        repo = _git_repo(tmp_path / "repo")
        proj = tmp_path / "projects"
        slug_dir = proj / workdir_to_slug(str(repo))
        slug_dir.mkdir(parents=True)
        (slug_dir / "new-sid.jsonl").write_text("")  # empty jsonl → offset 0
        monkeypatch.setattr("trowel_py.cc_host.service.cc_projects_root", lambda: proj)

        proc = FakeProc([line(init_event(sid="new-sid")), line(result_ok())])
        host = CCHost("sid", str(repo), spawner=FakeSpawner([proc]))
        events = await collect(host.send("first turn"))
        ts = next(e for e in events if isinstance(e, TurnStartEvent))
        assert ts.revertible is True
        assert host._session_start_saved is True
        saved = [
            m for m in checkpoint.list_checkpoints(str(repo)) if m.turn_id == ts.turn_id
        ]
        assert len(saved) == 1

    async def test_resumed_git_session_turn_is_revertible(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        repo = _git_repo(tmp_path / "repo")
        # fake the resumed jsonl under a tmp cc_projects_root
        proj = tmp_path / "projects"
        slug_dir = proj / workdir_to_slug(str(repo))
        slug_dir.mkdir(parents=True)
        (slug_dir / "cc-sid-1.jsonl").write_text(
            json.dumps({"type": "user", "message": {"role": "user", "content": "prior"}}) + "\n"
        )
        monkeypatch.setattr("trowel_py.cc_host.service.cc_projects_root", lambda: proj)

        proc = FakeProc([line(init_event(sid="cc-sid-1")), line(result_ok())])
        host = CCHost("sid", str(repo), resume_from="cc-sid-1", spawner=FakeSpawner([proc]))
        events = await collect(host.send("next turn"))
        ts = next(e for e in events if isinstance(e, TurnStartEvent))
        assert ts.revertible is True
        # the checkpoint was actually written to the repo's git
        saved = [m for m in checkpoint.list_checkpoints(str(repo)) if m.turn_id == ts.turn_id]
        assert len(saved) == 1
        assert saved[0].jsonl_offset is not None

    async def test_reload_kills_process_for_resume(self, tmp_path: Path):
        proc = FakeProc([line(init_event()), line(result_ok())])
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        await collect(host.send("hi"))
        assert host.is_dead is False  # process still alive after a clean turn
        await host.reload()
        assert host.is_dead is True
