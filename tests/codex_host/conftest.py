"""Codex Host 的共享 fixture；transport 测试装配位于其 support 模块。"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURE_DIR
