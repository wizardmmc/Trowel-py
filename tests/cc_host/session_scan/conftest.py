from pathlib import Path

import pytest

from trowel_py.cc_host import session_scan


@pytest.fixture(autouse=True)
def _isolated_projects_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    root = tmp_path / "projects"
    monkeypatch.setattr(session_scan, "cc_projects_root", lambda: root)
    return root


@pytest.fixture
def fake_projects(_isolated_projects_root: Path) -> Path:
    root = _isolated_projects_root
    session_dir = root / session_scan.workdir_to_slug("/workdir")
    session_dir.mkdir(parents=True)
    return session_dir
