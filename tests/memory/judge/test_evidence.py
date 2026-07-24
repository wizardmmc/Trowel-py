"""judge 检索证据归属。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.memory.judge.support import FINISHED, _VALID_DRAFT, _access, _session
import trowel_py.memory.judge as judge_module
from trowel_py.memory.access_log import AccessRecord, log_access
from trowel_py.memory.judge import judge_session
from trowel_py.memory.sessions_repo import SessionRecord


def test_summarize_uses_facade_access_log_patch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trowel_py.memory.attribution import AttributionIndex

    log_access(tmp_path, _access("judged-1", "search", query="真实记录"))
    monkeypatch.setattr(judge_module, "read_access_log", lambda root: [])

    summary = judge_module._summarize_access_log(
        tmp_path,
        "judged-1",
        AttributionIndex.from_root(tmp_path),
    )

    assert summary == "（该会话没有检索记录：没 search 也没 read）"


async def test_judge_prompt_only_sees_judged_session_access_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "memory"
    log_access(root, _access("judged-1", "search", query="缓存一致性"))
    log_access(root, _access("judged-1", "read", memory_id="real-note"))
    log_access(root, _access("eval-other", "search", query="不该出现"))

    captured: dict = {}

    class CaptureHost:
        def __init__(self, events):
            self._events = events

        async def send(self, prompt: str):
            captured["prompt"] = prompt
            for ev in self._events:
                yield ev

        async def close(self):
            pass

    def factory(session, workdir):
        (workdir / "judgement-draft.json").write_text(_VALID_DRAFT, encoding="utf-8")
        return CaptureHost([FINISHED])

    await judge_session(_session(), "2026-07-16", root, host_factory=factory)
    assert "缓存一致性" in captured["prompt"]
    assert "real-note" in captured["prompt"]
    assert "不该出现" not in captured["prompt"]


def test_summarize_pulls_pre_init_records_via_binding(tmp_path: Path) -> None:
    from trowel_py.memory.attribution import AttributionIndex
    from trowel_py.memory.judge import _summarize_access_log
    from trowel_py.memory.sessions_repo import (
        create_sessions_repository,
        open_sessions_db,
    )

    root = tmp_path / "memory"
    conn = open_sessions_db(root)
    try:
        create_sessions_repository(conn).register(
            SessionRecord(
                cc_session_id="cc-x",
                workdir="/p",
                date="2026-07-16",
                registered_at="t",
                session_kind="user",
                trowel_session_id="t1",
            )
        )
    finally:
        conn.close()
    log_access(
        root,
        AccessRecord(
            ts="t",
            trowel_session_id="t1",
            cc_session_id="",
            toolUseId="tu-1",
            action="search",
            search_id="s1",
            query="how to X",
            memory_id="m1",
            rank=0,
        ),
    )
    log_access(
        root,
        AccessRecord(
            ts="t",
            trowel_session_id="t1",
            cc_session_id="cc-x",
            toolUseId="tu-2",
            action="read",
            search_id="",
            read_id="r1",
            memory_id="m1",
        ),
    )
    log_access(
        root,
        AccessRecord(
            ts="t",
            trowel_session_id="t-other",
            cc_session_id="cc-other",
            toolUseId="tu-3",
            action="search",
            search_id="s2",
            query="unrelated query",
            memory_id="m2",
            rank=0,
        ),
    )
    index = AttributionIndex.from_root(root)
    summary = _summarize_access_log(root, "cc-x", index)
    assert "how to X" in summary
    assert "m1" in summary
    assert "unrelated query" not in summary
