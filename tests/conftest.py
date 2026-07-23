import sqlite3

import pytest
from fastapi.testclient import TestClient

from trowel_py.app import create_app


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
