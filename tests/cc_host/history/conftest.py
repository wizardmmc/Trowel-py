from pathlib import Path

import pytest

from trowel_py.cc_host import history


@pytest.fixture()
def fake_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "projects"
    slug_dir = root / history.workdir_to_slug("/workdir")
    slug_dir.mkdir(parents=True)
    monkeypatch.setattr(history, "cc_projects_root", lambda: root)
    return slug_dir
