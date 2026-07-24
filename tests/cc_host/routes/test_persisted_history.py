import json
from pathlib import Path

from tests.cc_host.routes.support import FakeHost, _mini_app


class TestListHistory:
    def test_list_returns_envelope_with_summaries(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        import uuid as uuidlib

        root = tmp_path / "projects"
        d = root / "-wd"
        d.mkdir(parents=True)
        # 可恢复会话必须使用 UUID 文件名，并包含真实用户消息。
        sid = str(uuidlib.uuid4())
        (d / f"{sid}.jsonl").write_text(
            json.dumps(
                {
                    "type": "user",
                    "isSidechain": False,
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "hi"}],
                    },
                }
            )
            + "\n"
        )
        monkeypatch.setattr(
            "trowel_py.cc_host.session_scan.cc_projects_root",
            lambda: root,
        )
        client = _mini_app({})
        resp = client.get("/api/cc/sessions", params={"workdir": "/wd"})
        assert resp.status_code == 200
        body = resp.json()
        data = body["data"]
        assert len(data) == 1
        assert data[0]["cc_session_id"] == sid
        assert data[0]["title"] == "hi"
        assert body["meta"] == {"total": 1, "limit": 10}

    def test_list_caps_to_ten_most_recent(self, tmp_path: Path, monkeypatch):
        import os
        import uuid as uuidlib

        root = tmp_path / "projects"
        d = root / "-wd"
        d.mkdir(parents=True)
        stems = []
        for i in range(12):
            stem = str(uuidlib.uuid4())
            stems.append(stem)
            f = d / f"{stem}.jsonl"
            f.write_text(
                json.dumps(
                    {
                        "type": "user",
                        "isSidechain": False,
                        "message": {
                            "role": "user",
                            "content": [{"type": "text", "text": f"s{i:02d}"}],
                        },
                    }
                )
                + "\n"
            )
            os.utime(f, (i, i))
        monkeypatch.setattr(
            "trowel_py.cc_host.session_scan.cc_projects_root",
            lambda: root,
        )
        client = _mini_app({})
        resp = client.get("/api/cc/sessions", params={"workdir": "/wd"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 10
        # 路由按 mtime 倒序返回十条，同时保留过滤后的总数。
        ids = [item["cc_session_id"] for item in data]
        assert ids[0] == stems[11]
        assert stems[0] not in ids and stems[1] not in ids
        assert resp.json()["meta"] == {"total": 12, "limit": 10}


class TestGetHistory:
    def test_returns_normalized_events_for_session(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        root = tmp_path / "projects"
        d = root / "-wd"
        d.mkdir(parents=True)
        (d / "sess-a.jsonl").write_text(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "hello"},
                }
            )
            + "\n"
            + json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "hi"}],
                    },
                }
            )
            + "\n"
        )
        # parse_history 持有导入后的引用，patch 必须指向其命名空间。
        monkeypatch.setattr(
            "trowel_py.cc_host.history.cc_projects_root",
            lambda: root,
        )
        fake = FakeHost([], cc_session_id="sess-a")
        reg = {"s-1": fake}
        client = _mini_app(reg)
        resp = client.get("/api/cc/sessions/s-1/history")
        assert resp.status_code == 200
        events = resp.json()["data"]
        kinds = [e["type"] for e in events]
        assert "user" in kinds
        assert "text" in kinds

    def test_history_unknown_session_404(self):
        client = _mini_app({})
        resp = client.get("/api/cc/sessions/nope/history")
        assert resp.status_code == 404

    def test_history_no_cc_session_id_returns_empty(self):
        fake = FakeHost([], cc_session_id=None)
        client = _mini_app({"s-1": fake})
        resp = client.get("/api/cc/sessions/s-1/history")
        assert resp.status_code == 200
        assert resp.json()["data"] == []
