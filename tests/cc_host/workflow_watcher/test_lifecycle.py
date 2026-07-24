from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.cc_host.workflow_watcher.support import set_mtime, write_wf
from trowel_py.cc_host import workflow_watcher
from trowel_py.cc_host.workflow_journal import JsonlCursor
from trowel_py.cc_host.workflow_watcher import WorkflowWatcher


def test_watcher_inert_until_enabled(tmp_path: Path) -> None:
    transcript = tmp_path / "session"
    workflow_dir = transcript / "workflows"
    watcher = WorkflowWatcher(transcript)
    assert watcher.poll() == []
    watcher.enable()
    assert watcher.poll() == []
    write_wf(workflow_dir, "wf_a")
    snapshots = watcher.poll()
    assert len(snapshots) == 1
    assert snapshots[0].run_id == "wf_a"


def test_watcher_dedups_unchanged_file(tmp_path: Path) -> None:
    transcript = tmp_path / "session"
    watcher = WorkflowWatcher(transcript)
    watcher.enable()
    path = write_wf(transcript / "workflows", "wf_a")
    set_mtime(path, 1000.0)
    assert len(watcher.poll()) == 1
    assert watcher.poll() == []


def test_watcher_emits_on_mtime_change(tmp_path: Path) -> None:
    transcript = tmp_path / "session"
    workflow_dir = transcript / "workflows"
    watcher = WorkflowWatcher(transcript)
    watcher.enable()
    path = write_wf(workflow_dir, "wf_a")
    set_mtime(path, 1000.0)
    assert watcher.poll()[0].status == "running"
    write_wf(workflow_dir, "wf_a", status="completed")
    set_mtime(path, 2000.0)
    snapshots = watcher.poll()
    assert len(snapshots) == 1
    assert snapshots[0].status == "completed"


def test_watcher_stops_polling_after_terminal_snapshot(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "session"
    watcher = WorkflowWatcher(transcript)
    watcher.enable()
    path = write_wf(
        transcript / "workflows",
        "wf_a",
        status="completed",
    )
    set_mtime(path, 1000.0)
    assert len(watcher.poll()) == 1
    set_mtime(path, 3000.0)
    assert watcher.poll() == []
    assert not watcher.is_watching


def test_watcher_discovers_new_runid_mid_run(tmp_path: Path) -> None:
    transcript = tmp_path / "session"
    workflow_dir = transcript / "workflows"
    write_wf(workflow_dir, "wf_a")
    watcher = WorkflowWatcher(transcript)
    watcher.enable()
    watcher.poll()
    write_wf(workflow_dir, "wf_b")
    assert [item.run_id for item in watcher.poll()] == ["wf_b"]


def test_watcher_vanished_file_dropped_silently(tmp_path: Path) -> None:
    transcript = tmp_path / "session"
    watcher = WorkflowWatcher(transcript)
    watcher.enable()
    path = write_wf(transcript / "workflows", "wf_a")
    set_mtime(path, 1000.0)
    watcher.poll()
    path.unlink()
    assert watcher.poll() == []
    assert not watcher.is_watching
    assert not watcher.all_done


def test_watcher_clears_vanished_run_state(tmp_path: Path) -> None:
    transcript = tmp_path / "session"
    workflow_dir = transcript / "workflows"
    workflow_dir.mkdir(parents=True)
    vanished_path = workflow_dir / "wf_gone.json"
    vanished_path.touch()
    vanished_path.unlink()
    watcher = WorkflowWatcher(transcript)
    watcher.enable()
    watcher._last_mtime.update({"wf_done": 1.0, "wf_gone": 2.0})
    watcher._finished.add("wf_done")
    watcher._pre_existing.add("wf_historical")
    watcher._tracked.update({"wf_done", "wf_gone"})
    watcher._journal_cursors["wf_gone"] = JsonlCursor()
    watcher._journal_agents["wf_gone"] = {}

    assert watcher.is_watching
    assert not watcher.all_done
    assert watcher.poll() == []

    assert "wf_gone" not in watcher._last_mtime
    assert "wf_gone" not in watcher._tracked
    assert "wf_gone" not in watcher._journal_cursors
    assert "wf_gone" not in watcher._journal_agents
    assert watcher._finished == {"wf_done"}
    assert watcher._pre_existing == {"wf_historical"}
    assert not watcher.is_watching
    assert watcher.all_done


def test_watcher_retries_same_mtime_after_parser_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transcript = tmp_path / "session"
    watcher = WorkflowWatcher(transcript)
    watcher.enable()
    workflow_dir = transcript / "workflows"
    workflow_dir.mkdir(parents=True)
    path = workflow_dir / "wf_retry.json"
    path.write_text("{}", encoding="utf-8")
    set_mtime(path, 1000.0)
    sentinel = SimpleNamespace(run_id="wf_retry", status="running")
    attempts = 0

    def parse(_payload: object) -> object:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError("injected parser failure")
        return sentinel

    monkeypatch.setattr(workflow_watcher, "parse_workflow_tree", parse)

    assert watcher.poll() == []
    assert "wf_retry" not in watcher._last_mtime
    assert watcher.poll() == [sentinel]
    assert attempts == 2
    assert watcher._last_mtime["wf_retry"] == 1000.0
    assert watcher.poll() == []
    assert attempts == 2


def test_close_clears_all_runtime_caches(tmp_path: Path) -> None:
    watcher = WorkflowWatcher(tmp_path)
    watcher.enable()
    watcher._last_mtime["wf_mtime"] = 1.0
    watcher._finished.add("wf_finished")
    watcher._pre_existing.add("wf_historical")
    watcher._tracked.add("wf_tracked")
    watcher._journal_cursors["wf_cursor"] = JsonlCursor()
    watcher._journal_agents["wf_agents"] = {}

    watcher.close()

    assert watcher._last_mtime == {}
    assert watcher._finished == set()
    assert watcher._pre_existing == set()
    assert watcher._tracked == set()
    assert watcher._journal_cursors == {}
    assert watcher._journal_agents == {}
    assert watcher.enabled
    assert watcher._dir == tmp_path
    assert not watcher.is_watching
    assert not watcher.all_done


def test_watcher_no_transcript_dir_yields_nothing(tmp_path: Path) -> None:
    watcher = WorkflowWatcher(None)
    watcher.enable()
    assert watcher.poll() == []


def test_watcher_skips_pre_existing_and_does_not_trip_all_done(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "session"
    workflow_dir = transcript / "workflows"
    old_path = write_wf(workflow_dir, "wf_old", status="completed")
    set_mtime(old_path, 500.0)
    watcher = WorkflowWatcher(transcript)
    watcher.enable()
    assert watcher.poll() == []
    assert not watcher.all_done

    write_wf(workflow_dir, "wf_new")
    snapshots = watcher.poll()
    assert [item.run_id for item in snapshots] == ["wf_new"]
    assert not watcher.all_done

    new_path = write_wf(workflow_dir, "wf_new", status="completed")
    set_mtime(new_path, 2000.0)
    watcher.poll()
    assert watcher.all_done
