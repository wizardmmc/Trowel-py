from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def forbid_real_home_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_cls: type[Path]) -> Path:
        raise AssertionError("slash item tests must pass isolated directories")

    monkeypatch.setattr(Path, "home", classmethod(fail))
