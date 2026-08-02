from __future__ import annotations

import fcntl
import json
import multiprocessing
from pathlib import Path
from typing import Any

from trowel_py.agent_host.binding import Runtime
from trowel_py.agent_host.title_store import (
    SessionTitleRecord,
    SessionTitleStore,
    resolve_title_store_path,
)


def _put_title(path: str, ready: Any, done: Any) -> None:
    """在独立进程中写入一个 Codex 标题。"""

    ready.set()
    SessionTitleStore(Path(path)).put(
        SessionTitleRecord(
            runtime=Runtime.CODEX,
            native_session_id="thread-from-child",
            title="子进程标题",
            source="generated",
            updated_at="2026-07-31T10:00:00",
        )
    )
    done.set()


def test_title_store_roundtrip_is_keyed_by_runtime_and_native_id(tmp_path):
    store = SessionTitleStore(tmp_path / "titles.json")
    store.put(
        SessionTitleRecord(
            runtime=Runtime.CLAUDE_CODE,
            native_session_id="same-id",
            title="Claude 标题",
            source="manual",
            updated_at="2026-07-31T10:00:00",
        )
    )
    store.put(
        SessionTitleRecord(
            runtime=Runtime.CODEX,
            native_session_id="same-id",
            title="Codex 标题",
            source="generated",
            updated_at="2026-07-31T10:01:00",
        )
    )

    restarted = SessionTitleStore(tmp_path / "titles.json")

    assert restarted.get(Runtime.CLAUDE_CODE, "same-id").title == "Claude 标题"
    assert restarted.get(Runtime.CODEX, "same-id").title == "Codex 标题"


def test_title_store_overwrites_one_native_title_without_losing_others(tmp_path):
    store = SessionTitleStore(tmp_path / "titles.json")
    first = SessionTitleRecord(
        runtime=Runtime.CODEX,
        native_session_id="thread-1",
        title="首个标题",
        source="generated",
        updated_at="2026-07-31T10:00:00",
    )
    other = SessionTitleRecord(
        runtime=Runtime.CODEX,
        native_session_id="thread-2",
        title="另一个标题",
        source="manual",
        updated_at="2026-07-31T10:01:00",
    )
    store.put(first)
    store.put(other)
    store.put(
        SessionTitleRecord(
            runtime=Runtime.CODEX,
            native_session_id="thread-1",
            title="改名后",
            source="manual",
            updated_at="2026-07-31T10:02:00",
        )
    )

    assert store.get(Runtime.CODEX, "thread-1").title == "改名后"
    assert store.get(Runtime.CODEX, "thread-2") == other


def test_title_store_ignores_corrupt_records(tmp_path):
    path = tmp_path / "titles.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "titles": {
                    "codex": {
                        "good": {
                            "title": "有效标题",
                            "source": "manual",
                            "updated_at": "now",
                        },
                        "bad": {"title": [], "source": "unknown"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    store = SessionTitleStore(path)

    assert store.get(Runtime.CODEX, "good").title == "有效标题"
    assert store.get(Runtime.CODEX, "bad") is None


def test_title_store_path_stays_next_to_binding_file(tmp_path):
    binding_path = tmp_path / "agent_sessions.json"

    assert resolve_title_store_path(binding_path) == (
        tmp_path / "agent_session_titles.json"
    )


def test_title_writer_waits_for_cross_process_read_modify_write_lock(tmp_path):
    path = tmp_path / "titles.json"
    lock_path = path.with_name(path.name + ".lock")
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    done = context.Event()
    process = context.Process(
        target=_put_title,
        args=(str(path), ready, done),
    )

    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        process.start()
        assert ready.wait(timeout=5)
        assert done.wait(timeout=0.2) is False
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    assert done.wait(timeout=5)
    process.join(timeout=5)
    assert process.exitcode == 0
    assert (
        SessionTitleStore(path).get(Runtime.CODEX, "thread-from-child").title
        == "子进程标题"
    )
