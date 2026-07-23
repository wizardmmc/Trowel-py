from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from trowel_py.cc_host import checkpoint
from trowel_py.cc_host.service import CCHost
from trowel_py.cc_host.session_scan import workdir_to_slug
from trowel_py.schemas.cc_host import FinishedEvent, TurnStartEvent

from tests.cc_host.service._support import (
    FakeProc,
    FakeSpawner,
    collect,
    init_event,
    line,
    result_ok,
)


def _git_repo(path: Path) -> Path:
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
        repo = _git_repo(tmp_path / "repo")
        proj = tmp_path / "projects"
        slug_dir = proj / workdir_to_slug(str(repo))
        slug_dir.mkdir(parents=True)
        (slug_dir / "new-sid.jsonl").write_text("")
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
        proj = tmp_path / "projects"
        slug_dir = proj / workdir_to_slug(str(repo))
        slug_dir.mkdir(parents=True)
        (slug_dir / "cc-sid-1.jsonl").write_text(
            json.dumps(
                {"type": "user", "message": {"role": "user", "content": "prior"}}
            )
            + "\n"
        )
        monkeypatch.setattr("trowel_py.cc_host.service.cc_projects_root", lambda: proj)

        proc = FakeProc([line(init_event(sid="cc-sid-1")), line(result_ok())])
        host = CCHost(
            "sid", str(repo), resume_from="cc-sid-1", spawner=FakeSpawner([proc])
        )
        events = await collect(host.send("next turn"))
        ts = next(e for e in events if isinstance(e, TurnStartEvent))
        assert ts.revertible is True
        saved = [
            m for m in checkpoint.list_checkpoints(str(repo)) if m.turn_id == ts.turn_id
        ]
        assert len(saved) == 1
        assert saved[0].jsonl_offset is not None

    async def test_reload_kills_process_for_resume(self, tmp_path: Path):
        proc = FakeProc([line(init_event()), line(result_ok())])
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        await collect(host.send("hi"))
        assert host.is_dead is False
        await host.reload()
        assert host.is_dead is True


# 真实时序中 all_done 后仍可能静默思考；只有 CC 的 result 才是 turn 边界。
class TestWorkflowTurnBoundary:
    async def test_all_done_silent_does_not_break_waits_for_result(
        self, tmp_path: Path
    ) -> None:
        proc = FakeProc([line(init_event())], feed_eof=False)
        host = CCHost(
            "sid",
            tmp_path,
            spawner=FakeSpawner([proc]),
            stalled_tick=0.001,
            stalled_threshold_mild=120.0,
            stalled_threshold_severe=300.0,
            stalled_threshold_kill=1800.0,
        )
        w = host._workflow_watcher
        w._enabled = True
        w._tracked.add("wf_x")
        w._finished.add("wf_x")
        assert w.enabled and w.all_done

        async def feed_result_after_silence() -> None:
            await asyncio.sleep(0.02)
            proc.stdout.feed_data(line(result_ok()).encode() + b"\n")

        task = asyncio.create_task(feed_result_after_silence())
        events = await collect(host.send("跑个 workflow"))
        await task
        assert any(isinstance(e, FinishedEvent) for e in events), (
            "all_done+silent 不应提前 break;send 应读到 cc 推的 result"
        )
