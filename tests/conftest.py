import sqlite3

import pytest
from fastapi.testclient import TestClient

from trowel_py.app import create_app


@pytest.fixture(autouse=True)
def _isolate_agent_session_files(tmp_path, monkeypatch):
    """阻止任何测试读取或写入用户真实的 Agent 会话索引。"""

    monkeypatch.setenv(
        "TROWEL_AGENT_SESSIONS_PATH",
        str(tmp_path / "agent_sessions.json"),
    )


@pytest.fixture
def db_connection():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def client():
    test_client = TestClient(create_app())
    try:
        yield test_client
    finally:
        test_client.close()
