"""验证桌面只读观察应用只开放真实数据统计查询。"""

from pathlib import Path

from fastapi.testclient import TestClient

from trowel_py.desktop.inspection import create_inspection_app


def test_inspection_app_reads_statistics_and_rejects_business_writes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """观察窗口可读空统计源，但不能调用任何业务写接口。"""

    read_root = tmp_path / "canonical"
    read_root.mkdir()
    monkeypatch.setenv("TROWEL_DESKTOP_READ_DATA_DIR", str(read_root))
    monkeypatch.setenv("TROWEL_APP_INSTANCE_ID", "inspection-instance")
    monkeypatch.setenv("TROWEL_DESKTOP_CREDENTIAL", "inspection-secret")
    monkeypatch.setenv(
        "TROWEL_DESKTOP_RENDERER_ORIGIN", "http://127.0.0.1:43124"
    )

    with TestClient(create_inspection_app()) as client:
        headers = {"Authorization": "Bearer inspection-secret"}
        health = client.get("/api/health", headers=headers)
        agent = client.get(
            "/api/statistics/agent",
            params={
                "start_date": "2026-08-01",
                "end_date": "2026-08-03",
                "timezone": "Asia/Shanghai",
            },
            headers=headers,
        )
        blocked = client.post("/api/agent/sessions", json={}, headers=headers)

    assert health.json()["data"] == {
        "status": "ok",
        "mode": "read-only-inspection",
    }
    assert agent.status_code == 200
    assert agent.json()["data"]["sample_size"] == 0
    assert blocked.status_code == 403
    assert blocked.json()["error"] == "read-only inspection mode"
