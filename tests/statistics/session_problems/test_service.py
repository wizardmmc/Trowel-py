"""验证会话问题 Statistics API 的时间窗、排序、游标和公开字段。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from trowel_py.memory.sessions_repo import (
    SessionProblemRecord,
    create_sessions_repository,
    open_sessions_db,
)
from trowel_py.statistics.routes import router
from trowel_py.statistics.session_problems.repository import (
    FileSessionProblemStatisticsReader,
)


def _save(
    root: Path,
    session_id: str,
    closed_at: str,
    problem_text: str | None,
) -> None:
    """在隔离 sessions.db 中保存一条完成记录。"""

    connection = open_sessions_db(root)
    try:
        repo = create_sessions_repository(connection)
        repo.review_requests.enqueue(
            session_id,
            runtime="claude_code",
            requested_at=closed_at[:19],
            closed_at=closed_at,
        )
        repo.session_problems.save_completed(
            SessionProblemRecord(
                trowel_session_id=session_id,
                runtime="claude_code",
                closed_at=closed_at,
                problem_text=problem_text,
                reviewed_at=closed_at,
                pipeline_version=1,
                run_id=f"run-{session_id}",
                generator_runtime="claude_code",
                generator_model="glm-5.1",
                generator_effort="",
                source_quality="reliable",
            )
        )
    finally:
        connection.close()


def test_session_problem_api_returns_nonempty_rows_newest_first(tmp_path: Path) -> None:
    """空结果不展示，相同关闭时间按 Trowel 会话 ID 稳定倒序。"""

    _save(tmp_path, "agent-a", "2026-08-03T12:00:00+08:00", "问题 A")
    _save(tmp_path, "agent-c", "2026-08-03T12:00:00+08:00", "问题 C")
    _save(tmp_path, "agent-b", "2026-08-03T11:00:00+08:00", "问题 B")
    _save(tmp_path, "agent-empty", "2026-08-03T13:00:00+08:00", None)
    _save(tmp_path, "agent-old", "2026-08-02T23:59:59+08:00", "窗外")
    app = FastAPI()
    app.state.session_problem_statistics_reader = FileSessionProblemStatisticsReader(
        tmp_path
    )
    app.include_router(router, prefix="/api/statistics")

    response = TestClient(app).get(
        "/api/statistics/session-problems",
        params={
            "start_date": "2026-08-03",
            "end_date": "2026-08-03",
            "timezone": "Asia/Shanghai",
            "limit": "2",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["reviewed_session_count"] == 4
    assert data["problem_count"] == 3
    assert [item["trowel_session_id"] for item in data["items"]] == [
        "agent-c",
        "agent-a",
    ]
    assert data["next_cursor"] is not None
    assert set(data["items"][0]) == {
        "trowel_session_id",
        "runtime",
        "closed_at",
        "problem_text",
    }
    assert "glm-5.1" not in response.text
    assert "run-agent" not in response.text

    second = TestClient(app).get(
        "/api/statistics/session-problems",
        params={
            "start_date": "2026-08-03",
            "end_date": "2026-08-03",
            "timezone": "Asia/Shanghai",
            "limit": "2",
            "cursor": data["next_cursor"],
        },
    )
    assert [item["trowel_session_id"] for item in second.json()["data"]["items"]] == [
        "agent-b"
    ]


def test_session_problem_api_reports_missing_reader() -> None:
    """未装配 Memory 只读来源时沿用统一错误 envelope。"""

    app = FastAPI()
    app.include_router(router, prefix="/api/statistics")

    response = TestClient(app).get(
        "/api/statistics/session-problems",
        params={
            "start_date": "2026-08-03",
            "end_date": "2026-08-03",
            "timezone": "UTC",
        },
    )

    assert response.status_code == 503
    assert response.json()["error"] == "statistics session problems source unavailable"


def test_session_problem_reader_does_not_migrate_upgrade_pending_database(
    tmp_path: Path,
) -> None:
    """统计读取升级前数据库时返回 unavailable，不能偷偷创建业务表。"""

    meta = tmp_path / "meta"
    meta.mkdir()
    database = meta / "sessions.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE legacy_sessions (id TEXT PRIMARY KEY)")
    connection.commit()
    connection.close()
    app = FastAPI()
    app.state.session_problem_statistics_reader = FileSessionProblemStatisticsReader(
        tmp_path
    )
    app.include_router(router, prefix="/api/statistics")

    response = TestClient(app).get(
        "/api/statistics/session-problems",
        params={
            "start_date": "2026-08-03",
            "end_date": "2026-08-03",
            "timezone": "UTC",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["reviewed_session_count"] == 0
    assert data["quality"] == "unavailable"
    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            )
        }
        assert tables == {"legacy_sessions"}
    finally:
        connection.close()
