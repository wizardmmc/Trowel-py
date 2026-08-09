"""验证真实 moon 反向 runner 不会选中外层 workspace 或 PR revision。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from tests.quality.reverse_runner import _isolated_environment


def test_fixture_environment_removes_parent_workspace_and_ci_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """隔离 Git 仓库不得继承外层 moon 或 GitHub PR 上下文。

    Args:
        monkeypatch: 注入能够让 fixture 错选外层仓库和分支的环境变量。
    """
    monkeypatch.setenv("MOON_WORKSPACE_ROOT", "/outer/workspace")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_BASE_REF", "integration-branch")
    monkeypatch.setenv("CI", "true")

    environment = _isolated_environment()

    assert not any(key.startswith(("MOON_", "GITHUB_")) for key in environment)
    assert "CI" not in environment
    assert environment["PATH"].split(os.pathsep)[0] == str(
        Path(sys.executable).resolve().parent
    )
