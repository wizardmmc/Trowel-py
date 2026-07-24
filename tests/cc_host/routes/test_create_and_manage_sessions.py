from pathlib import Path

from trowel_py.cc_host import routes as cc_routes
from tests.cc_host.routes.support import FakeHost, _mini_app


class TestCreateSession:
    def test_creates_session_returns_envelope(self, tmp_path: Path):
        reg: dict = {}
        client = _mini_app(reg)
        resp = client.post("/api/cc/sessions", json={"workdir": str(tmp_path)})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        sid = body["data"]["session_id"]
        assert sid in reg
        assert body["data"]["model"] is None

    def test_create_with_resume(self, tmp_path: Path):
        reg: dict = {}
        client = _mini_app(reg)
        resp = client.post(
            "/api/cc/sessions",
            json={"workdir": str(tmp_path), "resume_from": "abc"},
        )
        assert resp.json()["data"]["cc_session_id"] == "abc"

    def test_create_rejects_missing_workdir(self):
        client = _mini_app({})
        resp = client.post("/api/cc/sessions", json={"workdir": "/no/such/dir/here"})
        assert resp.status_code == 400


class TestSessionSwitches:
    def test_defaults_to_both_on(self, tmp_path: Path):
        client = _mini_app({})
        resp = client.post("/api/cc/sessions", json={"workdir": str(tmp_path)})
        data = resp.json()["data"]
        assert data["memory_enabled"] is True
        assert data["profile_enabled"] is True

    def test_explicit_four_combos_round_trip(self, tmp_path: Path):
        client = _mini_app({})
        for memory_enabled, profile_enabled in [
            (True, True),
            (True, False),
            (False, True),
            (False, False),
        ]:
            resp = client.post(
                "/api/cc/sessions",
                json={
                    "workdir": str(tmp_path),
                    "memory_enabled": memory_enabled,
                    "profile_enabled": profile_enabled,
                },
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["memory_enabled"] is memory_enabled
            assert data["profile_enabled"] is profile_enabled

    def test_non_boolean_rejected_as_422(self, tmp_path: Path):
        client = _mini_app({})
        resp = client.post(
            "/api/cc/sessions",
            json={"workdir": str(tmp_path), "memory_enabled": "yes"},
        )
        assert resp.status_code == 422

    def test_active_sessions_carry_switches(self, tmp_path: Path):
        client = _mini_app({})
        client.post(
            "/api/cc/sessions",
            json={
                "workdir": str(tmp_path),
                "memory_enabled": False,
                "profile_enabled": True,
            },
        )
        sessions = client.get("/api/cc/sessions/active").json()["data"]["sessions"]
        assert len(sessions) == 1
        assert sessions[0]["memory_enabled"] is False
        assert sessions[0]["profile_enabled"] is True

    def test_memory_off_session_has_no_mcp_config(self, tmp_path: Path):
        reg: dict = {}
        client = _mini_app(reg)
        sid = client.post(
            "/api/cc/sessions",
            json={"workdir": str(tmp_path), "memory_enabled": False},
        ).json()["data"]["session_id"]
        host = reg[sid]
        assert host._mcp_config is None
        assert host.memory_enabled is False


class TestMultiSession:
    def test_same_workdir_sessions_get_suffix(self, tmp_path: Path):
        client = _mini_app({})
        client.post("/api/cc/sessions", json={"workdir": str(tmp_path)})
        client.post("/api/cc/sessions", json={"workdir": str(tmp_path)})
        data = client.get("/api/cc/sessions/active").json()["data"]
        names = sorted(s["name"] for s in data["sessions"])
        assert tmp_path.name in names
        assert f"{tmp_path.name} #2" in names

    def test_active_returns_sessions_and_active_id(self, tmp_path: Path):
        client = _mini_app({})
        sid = client.post("/api/cc/sessions", json={"workdir": str(tmp_path)}).json()[
            "data"
        ]["session_id"]
        data = client.get("/api/cc/sessions/active").json()["data"]
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["id"] == sid
        assert data["sessions"][0]["workdir"] == str(tmp_path)
        assert data["active_id"] == sid

    def test_active_lists_connected_flag(self, tmp_path: Path):
        reg: dict = {}
        client = _mini_app(reg)
        temp_sid = client.post(
            "/api/cc/sessions", json={"workdir": str(tmp_path)}
        ).json()["data"]["session_id"]
        live_host = FakeHost(events=[], workdir=str(tmp_path))
        live_sid = "live-fake-1"
        reg[live_sid] = live_host
        cc_routes._WORKDIR_INDEX.setdefault(str(tmp_path), set()).add(live_sid)
        cc_routes._SESSION_NAMES[live_sid] = tmp_path.name

        data = client.get("/api/cc/sessions/active").json()["data"]
        by_id = {s["id"]: s for s in data["sessions"]}
        assert by_id[temp_sid]["connected"] is False
        assert by_id[live_sid]["connected"] is True

    def test_activate_switches_active(self, tmp_path: Path):
        client = _mini_app({})
        sid1 = client.post("/api/cc/sessions", json={"workdir": str(tmp_path)}).json()[
            "data"
        ]["session_id"]
        sid2 = client.post(  # noqa: F841 — 第二次创建用于建立多会话状态。
            "/api/cc/sessions", json={"workdir": str(tmp_path)}
        ).json()["data"]["session_id"]
        resp = client.post(f"/api/cc/sessions/{sid1}/activate")
        assert resp.json()["data"]["active_id"] == sid1
        assert client.get("/api/cc/sessions/active").json()["data"]["active_id"] == sid1

    def test_activate_unknown_session_404(self, tmp_path: Path):
        client = _mini_app({})
        resp = client.post("/api/cc/sessions/nope/activate")
        assert resp.status_code == 404

    def test_create_rejects_over_connection_limit(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(cc_routes, "MAX_CONNECTIONS", 2)
        client = _mini_app({})
        client.post("/api/cc/sessions", json={"workdir": str(tmp_path)})
        client.post("/api/cc/sessions", json={"workdir": str(tmp_path)})
        resp = client.post("/api/cc/sessions", json={"workdir": str(tmp_path)})
        assert resp.status_code == 409

    def test_delete_clears_multi_state(self, tmp_path: Path):
        client = _mini_app({})
        sid = client.post("/api/cc/sessions", json={"workdir": str(tmp_path)}).json()[
            "data"
        ]["session_id"]
        resp = client.delete(f"/api/cc/sessions/{sid}")
        assert resp.json()["success"]
        data = client.get("/api/cc/sessions/active").json()["data"]
        assert data["sessions"] == []
        assert data["active_id"] is None
        assert cc_routes._WORKDIR_INDEX.get(str(tmp_path)) is None
