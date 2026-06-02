import sqlite3
import pytest
from trowel_py.app import create_app
from fastapi.testclient import TestClient

# 定义测试配置
@pytest.fixture # 装饰器，把函数标记为测试夹具
def db_connection():
    conn = sqlite3.connect(":memory:")  # :memory: - 特殊写法，表示用内存数据库。注：内存数据库不需要读不被写阻塞
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    yield conn  # yield 将 conn 交给测试用，测试跑完后继续执行下面的代码
    conn.close()

@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)

