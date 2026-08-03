"""验证会话问题处理与 Memory 增量提炼彼此独立。"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.memory.daily_review.support import FINISHED, VALID_DRAFT, FakeHost, session
from trowel_py.memory.daily_review.problems.service import process_session_problem
from trowel_py.memory.review_job import run_daily_review
from trowel_py.memory.sessions_repo import create_sessions_repository, open_sessions_db

ERROR = SimpleNamespace(type="error")


class _WritingProblemHost:
    """按测试给定内容写入问题 JSON，并记录模型调用次数。"""

    def __init__(self, workdir: Path, output: object, calls: list[str]) -> None:
        self._workdir = workdir
        self._output = output
        self._calls = calls
        self.model = "glm-5.1"
        self.session_id = "problem-service-run"

    async def send(self, _prompt: str):
        self._calls.append(self._workdir.name)
        (self._workdir / "problem.json").write_text(
            json.dumps({"problem": self._output}, ensure_ascii=False),
            encoding="utf-8",
        )
        yield FINISHED

    async def close(self) -> None:
        """测试 host 不持有外部资源。"""


async def test_completed_session_problem_skips_nine_retries(tmp_path: Path) -> None:
    """第一次完成后，同一关闭请求的九次重放都不能再次调用模型。"""

    memory_root = tmp_path / "memory"
    connection = open_sessions_db(memory_root)
    try:
        repo = create_sessions_repository(connection)
        recorded = (
            Path(__file__).parents[2]
            / "statistics"
            / "fixtures"
            / "cc-binding-2.1.197.jsonl"
        )
        repo.claude.register(
            replace(
                session("native", "/isolated/project"),
                trowel_session_id="agent-once",
                jsonl_path=str(recorded),
            )
        )
        repo.claude.update_completed("native", recorded.stat().st_size)
        repo.review_requests.enqueue(
            "agent-once",
            runtime="claude_code",
            requested_at="2026-08-03T12:00:00",
            closed_at="2026-08-03T12:00:00+08:00",
            expected_native_session_id="native",
        )
        request = repo.review_requests.find("agent-once")
        assert request is not None
        calls: list[str] = []

        def factory(_session, workdir):
            return _WritingProblemHost(workdir, "没有先核对真实协议。", calls)

        for _ in range(10):
            assert await process_session_problem(
                memory_root,
                "2026-08-03",
                repo,
                request,
                host_factory=factory,
                now_fn=lambda: datetime.fromisoformat("2026-08-03T12:05:00+08:00"),
            )

        assert len(calls) == 1
        stored = repo.session_problems.find("agent-once")
        assert stored is not None
        assert stored.problem_text == "没有先核对真实协议。"
    finally:
        connection.close()


async def test_problem_failure_does_not_roll_back_memory_watermark(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """问题分析失败后关闭请求保留，但 Note/Episode 的水位正常推进。"""

    memory_root = tmp_path / "memory"
    recorded = (
        Path(__file__).parents[2]
        / "statistics"
        / "fixtures"
        / "cc-binding-2.1.197.jsonl"
    )
    connection = open_sessions_db(memory_root)
    repo = create_sessions_repository(connection)
    repo.claude.register(
        replace(
            session("native", "/isolated/project"),
            trowel_session_id="agent-fail",
            jsonl_path=str(recorded),
        )
    )
    repo.claude.update_completed("native", recorded.stat().st_size)
    repo.review_requests.enqueue(
        "agent-fail",
        runtime="claude_code",
        requested_at="2026-07-14T12:00:00",
        closed_at="2026-07-14T12:00:00+08:00",
        expected_native_session_id="native",
    )
    connection.close()
    monkeypatch.setattr(
        "trowel_py.memory.daily_review.batch._compress_or_aggregate",
        lambda _root, _day, _provider: None,
    )
    monkeypatch.setattr(
        "trowel_py.memory.daily_review.batch._maintain_dictionary",
        lambda _root, _provider: None,
    )

    def factory(_session, workdir):
        if "session-problems" in workdir.parts:
            return FakeHost([ERROR])
        (workdir / "draft.json").write_text(
            VALID_DRAFT.replace("2026-07-09", "2026-07-14"),
            encoding="utf-8",
        )
        return FakeHost([FINISHED])

    await run_daily_review(
        event={"review_session_id": "agent-fail"},
        memory_root=memory_root,
        date_str="2026-07-14",
        host_factory=factory,
    )

    connection = open_sessions_db(memory_root)
    try:
        repo = create_sessions_repository(connection)
        assert repo.claude.list_pending_segments() == []
        assert repo.session_problems.find("agent-fail") is None
        assert repo.review_requests.find("agent-fail") is not None
    finally:
        connection.close()


async def test_problem_failure_log_does_not_echo_exception_text(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """未知 host 异常可能含路径或凭据，日志只记录异常类别。"""

    memory_root = tmp_path / "memory"
    recorded = (
        Path(__file__).parents[2]
        / "statistics"
        / "fixtures"
        / "cc-binding-2.1.197.jsonl"
    )
    connection = open_sessions_db(memory_root)
    try:
        repo = create_sessions_repository(connection)
        repo.claude.register(
            replace(
                session("native-private", "/isolated/project"),
                trowel_session_id="agent-log",
                jsonl_path=str(recorded),
            )
        )
        repo.claude.update_completed("native-private", recorded.stat().st_size)
        repo.review_requests.enqueue(
            "agent-log",
            runtime="claude_code",
            requested_at="2026-08-03T12:00:00",
            closed_at="2026-08-03T12:00:00+08:00",
            expected_native_session_id="native-private",
        )
        request = repo.review_requests.find("agent-log")
        assert request is not None

        def failing_factory(_session, _workdir):
            raise RuntimeError("do-not-log-/private/credential")

        with caplog.at_level(logging.WARNING, logger="trowel_py.memory.review_job"):
            completed = await process_session_problem(
                memory_root,
                "2026-08-03",
                repo,
                request,
                host_factory=failing_factory,
            )

        assert completed is False
        assert "RuntimeError" in caplog.text
        assert "do-not-log" not in caplog.text
        assert "/private/credential" not in caplog.text
    finally:
        connection.close()
