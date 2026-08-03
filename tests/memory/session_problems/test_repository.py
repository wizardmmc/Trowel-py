"""验证会话复盘问题的一会话一记录和关闭请求完成语义。"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from trowel_py.memory.sessions_repo import (
    SessionProblemRecord,
    SessionsRepository,
    create_sessions_repository,
    open_sessions_db,
)


def _record(
    session_id: str,
    *,
    problem_text: str | None,
    closed_at: str = "2026-08-03T12:00:00+08:00",
) -> SessionProblemRecord:
    """构造只含脱敏文本的会话问题记录。"""

    return SessionProblemRecord(
        trowel_session_id=session_id,
        runtime="claude_code",
        closed_at=closed_at,
        problem_text=problem_text,
        reviewed_at="2026-08-03T12:05:00+08:00",
        pipeline_version=1,
        run_id="review-run",
        generator_runtime="claude_code",
        generator_model="glm-5.1",
        generator_effort="",
        source_quality="reliable",
    )


def test_completed_problem_atomically_marks_close_request(tmp_path: Path) -> None:
    """问题记录和关闭请求完成标记必须在同一个 SQLite 提交中落地。"""

    connection = open_sessions_db(tmp_path)
    try:
        repo = create_sessions_repository(connection)
        repo.review_requests.enqueue(
            "agent-1",
            runtime="claude_code",
            requested_at="2026-08-03T12:00:00",
            closed_at="2026-08-03T12:00:00+08:00",
        )

        created = repo.session_problems.save_completed(
            _record("agent-1", problem_text="实现前没有验证来源协议。")
        )

        assert created is True
        stored = repo.session_problems.find("agent-1")
        assert stored is not None
        assert stored.problem_text == "实现前没有验证来源协议。"
        request = repo.review_requests.find("agent-1")
        assert request is not None
        assert request.problem_recorded_at == "2026-08-03T12:05:00+08:00"
    finally:
        connection.close()


def test_null_problem_is_completed_and_retry_is_idempotent(tmp_path: Path) -> None:
    """没有问题也要留下完成收据，重试不能改写第一次结果。"""

    connection = open_sessions_db(tmp_path)
    try:
        repo = create_sessions_repository(connection)
        repo.review_requests.enqueue(
            "agent-clean",
            runtime="codex",
            requested_at="2026-08-03T12:00:00",
            closed_at="2026-08-03T12:00:00+08:00",
        )
        first = repo.session_problems.save_completed(
            _record("agent-clean", problem_text=None)
        )
        second = repo.session_problems.save_completed(
            _record("agent-clean", problem_text="重试产生了不同答案。")
        )

        assert first is True
        assert second is False
        stored = repo.session_problems.find("agent-clean")
        assert stored is not None
        assert stored.problem_text is None
        assert repo.review_requests.complete_satisfied() == ("agent-clean",)
    finally:
        connection.close()


def test_identical_text_from_two_sessions_remains_two_records(tmp_path: Path) -> None:
    """相同问题文本不执行跨会话语义合并。"""

    connection = open_sessions_db(tmp_path)
    try:
        repo = create_sessions_repository(connection)
        for session_id in ("agent-a", "agent-b"):
            repo.review_requests.enqueue(
                session_id,
                runtime="claude_code",
                requested_at="2026-08-03T12:00:00",
                closed_at="2026-08-03T12:00:00+08:00",
            )
            repo.session_problems.save_completed(
                _record(session_id, problem_text="没有先做真实 spike。")
            )

        assert [
            item.trowel_session_id for item in repo.session_problems.list_all()
        ] == ["agent-a", "agent-b"]
    finally:
        connection.close()


@pytest.mark.parametrize("problem_text", ["x" * 2001, "包含\x00非法字符"])
def test_repository_rejects_problem_text_outside_public_bounds(
    tmp_path: Path,
    problem_text: str,
) -> None:
    """绕过 Agent 的写入也不能突破问题文本的公开存储边界。"""

    connection = open_sessions_db(tmp_path)
    try:
        repo = create_sessions_repository(connection)
        repo.review_requests.enqueue(
            "agent-invalid",
            runtime="claude_code",
            requested_at="2026-08-03T12:00:00",
            closed_at="2026-08-03T12:00:00+08:00",
        )

        with pytest.raises(ValueError):
            repo.session_problems.save_completed(
                replace(
                    _record("agent-invalid", problem_text=None),
                    problem_text=problem_text,
                )
            )

        assert repo.session_problems.find("agent-invalid") is None
        request = repo.review_requests.find("agent-invalid")
        assert request is not None
        assert request.problem_recorded_at is None
    finally:
        connection.close()


def test_concurrent_problem_saves_keep_one_first_result(tmp_path: Path) -> None:
    """两个独立连接并发完成同一请求时只创建一条不可改写记录。"""

    connection = open_sessions_db(tmp_path)
    try:
        repo = create_sessions_repository(connection)
        repo.review_requests.enqueue(
            "agent-race",
            runtime="claude_code",
            requested_at="2026-08-03T12:00:00",
            closed_at="2026-08-03T12:00:00+08:00",
        )
    finally:
        connection.close()
    barrier = threading.Barrier(2)

    def save(problem_text: str) -> tuple[str, bool]:
        worker_connection = open_sessions_db(tmp_path)
        try:
            worker_repo = SessionsRepository(worker_connection, migrate=False)
            barrier.wait()
            return problem_text, worker_repo.session_problems.save_completed(
                _record("agent-race", problem_text=problem_text)
            )
        finally:
            worker_connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, ("并发结果 A", "并发结果 B")))

    assert sorted(created for _, created in results) == [False, True]
    winning_text = next(text for text, created in results if created)
    connection = open_sessions_db(tmp_path)
    try:
        repo = create_sessions_repository(connection)
        stored = repo.session_problems.find("agent-race")
        assert stored is not None
        assert stored.problem_text == winning_text
        request = repo.review_requests.find("agent-race")
        assert request is not None
        assert request.problem_recorded_at is not None
    finally:
        connection.close()
