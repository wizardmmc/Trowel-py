from pathlib import Path

from trowel_py.cc_host import routes as cc_routes
from tests.cc_host.routes.support import _mini_app


class TestModelsEndpoint:
    def test_returns_alias_envelope(self, monkeypatch):
        from trowel_py.cc_host.models import ModelOption

        monkeypatch.setattr(
            "trowel_py.cc_host.routes.list_models",
            lambda: [
                ModelOption(
                    value="opus",
                    label="Opus",
                    real_model="glm-5.2[1M]",
                    description="最强推理",
                )
            ],
        )
        client = _mini_app({})
        resp = client.get("/api/cc/models")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["error"] is None
        assert body["data"][0]["value"] == "opus"
        assert body["data"][0]["label"] == "Opus"
        assert body["data"][0]["real_model"] == "glm-5.2[1M]"

    def test_empty_envelope_when_no_settings(self, monkeypatch):
        monkeypatch.setattr("trowel_py.cc_host.routes.list_models", lambda: [])
        client = _mini_app({})
        resp = client.get("/api/cc/models")
        assert resp.status_code == 200
        assert resp.json()["data"] == []


class TestSlashItemsEndpoint:
    def test_returns_items_envelope(self, monkeypatch):
        from trowel_py.cc_host.slash_items import SlashItem

        monkeypatch.setattr(
            "trowel_py.cc_host.routes.list_slash_items",
            lambda workdir, init_roster=None: [
                SlashItem(
                    name="monthly-etf",
                    description="月度ETF",
                    source="user",
                    type="skill",
                )
            ],
        )
        client = _mini_app({})
        resp = client.get("/api/cc/slash-items?workdir=/wd")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["error"] is None
        assert body["data"][0]["name"] == "monthly-etf"
        assert body["data"][0]["type"] == "skill"

    def test_requires_workdir_query(self, monkeypatch):
        client = _mini_app({})
        resp = client.get("/api/cc/slash-items")
        assert resp.status_code == 422

    def test_passes_active_session_init_roster_as_floor(
        self,
        monkeypatch,
        tmp_path,
    ):
        import types as _types

        captured: dict = {}

        def fake_list(workdir, init_roster=None):
            captured["init_roster"] = init_roster
            return []

        monkeypatch.setattr(
            "trowel_py.cc_host.routes.list_slash_items",
            fake_list,
        )
        fake_host = _types.SimpleNamespace(
            _init_roster=["deep-research", "everything-claude-code:aside"],
        )
        sid = "s-init"
        client = _mini_app({sid: fake_host})
        cc_routes._WORKDIR_INDEX[str(tmp_path)] = {sid}
        cc_routes._ACTIVE_SID = sid
        resp = client.get(f"/api/cc/slash-items?workdir={tmp_path}")
        assert resp.status_code == 200
        assert captured["init_roster"] == [
            "deep-research",
            "everything-claude-code:aside",
        ]

    def test_no_active_session_yields_empty_roster(self, monkeypatch, tmp_path):
        captured: dict = {}

        def fake_list(workdir, init_roster=None):
            captured["init_roster"] = init_roster
            return []

        monkeypatch.setattr(
            "trowel_py.cc_host.routes.list_slash_items",
            fake_list,
        )
        client = _mini_app({})
        resp = client.get(f"/api/cc/slash-items?workdir={tmp_path}")
        assert resp.status_code == 200
        assert captured["init_roster"] == []


class TestListDirEndpoint:
    def test_lists_immediate_subdirectories_sorted(self, tmp_path: Path):
        (tmp_path / "b").mkdir()
        (tmp_path / "a").mkdir()
        (tmp_path / "file.txt").write_text("x")
        (tmp_path / ".hidden").mkdir()
        client = _mini_app({})
        resp = client.get(f"/api/cc/list-dir?path={tmp_path}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert [c["name"] for c in body["data"]] == ["a", "b"]

    def test_child_carries_absolute_path(self, tmp_path: Path):
        (tmp_path / "sub").mkdir()
        client = _mini_app({})
        resp = client.get(f"/api/cc/list-dir?path={tmp_path}")
        child = resp.json()["data"][0]
        assert child["name"] == "sub"
        assert child["path"] == str(tmp_path / "sub")

    def test_nonexistent_path_returns_400(self, tmp_path: Path):
        client = _mini_app({})
        resp = client.get(f"/api/cc/list-dir?path={tmp_path / 'nope'}")
        assert resp.status_code == 400

    def test_file_path_returns_400(self, tmp_path: Path):
        f = tmp_path / "f.txt"
        f.write_text("x")
        client = _mini_app({})
        resp = client.get(f"/api/cc/list-dir?path={f}")
        assert resp.status_code == 400

    def test_expands_tilde_to_home(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / "realdir").mkdir()
        client = _mini_app({})
        resp = client.get("/api/cc/list-dir?path=~")
        assert resp.status_code == 200
        assert [c["name"] for c in resp.json()["data"]] == ["realdir"]
