"""Shared fixtures for the codex_host suite.

Real-subprocess tests live in ``test_integration.py`` and are marked
``integration`` (deselected by default; see ``pyproject.toml`` → ``addopts``).
Unit tests build their own ``(client, fake)`` pair via the local ``_build``
helper in ``test_transport.py`` so each case is self-contained.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Directory of redacted real recordings (initialize / notifications …)."""

    return FIXTURE_DIR
