from fastapi.testclient import TestClient

def test_health_return_ok(client: TestClient):  # 自动执行/注入 conftest.py 的 client() 函数
    """
    验证 /api/health 接口返回的内容符合预期
    """
    response = client.get("/api/health")    # 模拟get请求
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "ok"
    assert body["error"] is None
