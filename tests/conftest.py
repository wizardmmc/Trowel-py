"""提供全测试套件共用的数据库和进程环境隔离。"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from trowel_py.app import create_app


@pytest.fixture(autouse=True)
def _isolate_trowel_process_environment(tmp_path, monkeypatch):
    """阻止测试继承桌面宿主路径或读写用户真实的 Trowel 数据。"""

    for variable in (
        "TROWEL_APP_INSTANCE_ID",
        "TROWEL_DESKTOP_CREDENTIAL",
        "TROWEL_DESKTOP_DATA_DIR",
        "TROWEL_DESKTOP_DATA_MODE",
        "TROWEL_DESKTOP_LOG_DIR",
        "TROWEL_DESKTOP_RENDERER_ORIGIN",
        "TROWEL_PROJECT_ROOT",
        "TROWEL_SERVER_PORT",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("TROWEL_DATA_ROOT", str(tmp_path / "trowel-data"))

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
