"""验证公共项目上下文叶在本地与 CI 使用正确严格度。"""

from __future__ import annotations

from scripts.quality.context_task import context_check_arguments


def test_context_check_allows_untracked_files_during_local_development() -> None:
    """本地 Gate 必须允许当前 slice 尚未提交的新文件。"""
    assert context_check_arguments({})[-1] == "--allow-untracked"


def test_context_check_uses_strict_snapshot_in_ci() -> None:
    """CI 必须检查 Git 跟踪状态，不能沿用本地宽松模式。"""
    assert "--allow-untracked" not in context_check_arguments({"CI": "true"})
