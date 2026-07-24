import subprocess
from pathlib import Path

from tests.cc_host.routes.support import FakeHost, _mini_app


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "seed").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)
    return path


class TestRevert:
    def test_create_session_revert_enabled_true_for_git(self, tmp_path: Path):
        _git_repo(tmp_path / "repo")
        client = _mini_app({})
        resp = client.post(
            "/api/cc/sessions",
            json={"workdir": str(tmp_path / "repo")},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["revert_enabled"] is True

    def test_create_session_revert_enabled_false_for_non_git(self, tmp_path: Path):
        norepo = tmp_path / "norepo"
        norepo.mkdir()
        client = _mini_app({})
        resp = client.post("/api/cc/sessions", json={"workdir": str(norepo)})
        assert resp.json()["data"]["revert_enabled"] is False

    def test_revert_unknown_turn_404(self, tmp_path: Path):
        repo = _git_repo(tmp_path / "repo")
        sid = "s1"
        host = FakeHost(events=[], workdir=str(repo))
        client = _mini_app({sid: host})
        resp = client.post(
            f"/api/cc/sessions/{sid}/revert",
            json={"turn_id": "nope"},
        )
        assert resp.status_code == 404
        assert host.reloaded is False

    def test_revert_non_git_400(self, tmp_path: Path):
        norepo = tmp_path / "norepo"
        norepo.mkdir()
        sid = "s1"
        host = FakeHost(events=[], workdir=str(norepo))
        client = _mini_app({sid: host})
        resp = client.post(
            f"/api/cc/sessions/{sid}/revert",
            json={"turn_id": "x"},
        )
        assert resp.status_code == 400

    def test_revert_restores_files_and_reloads_process(self, tmp_path: Path):
        import uuid as uuidlib

        repo = _git_repo(tmp_path / "repo")
        # 先保存 checkpoint，再模拟该轮对已跟踪文件的修改。
        turn_id = uuidlib.uuid4().hex
        ckpt_dir = tmp_path / "ckpt_src"
        ckpt_dir.mkdir()
        from trowel_py.cc_host import checkpoint as ckpt

        ckpt.save(str(repo), turn_id)
        (repo / "seed").write_text("changed during the turn\n")
        assert repo.joinpath("seed").read_text() != "x\n"

        sid = "s1"
        host = FakeHost(events=[], workdir=str(repo))
        client = _mini_app({sid: host})
        resp = client.post(
            f"/api/cc/sessions/{sid}/revert",
            json={"turn_id": turn_id},
        )
        assert resp.status_code == 200
        assert (repo / "seed").read_text() == "x\n"
        assert host.reloaded is True
        assert resp.json()["data"]["reverted_turn_id"] == turn_id
