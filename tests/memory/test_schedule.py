"""tests for the launchd scheduling module (slice-040-b T14).

All tests stub out ``subprocess.run`` so nothing touches the real launchctl,
and pass ``home=tmp_path`` / explicit paths so nothing touches the real
``~/Library/LaunchAgents``.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from trowel_py.memory import schedule


@pytest.fixture(autouse=True)
def _no_launchctl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub launchctl calls; status() sees returncode=1 (not loaded)."""
    monkeypatch.setattr(
        schedule.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout=b"", stderr=b""),
    )


def test_render_plist_absolute_interpreter_and_workdir() -> None:
    plist = schedule.render_plist(
        "02:30", interpreter="/py/bin/python", workdir="/wd", log_dir="/log"
    )
    assert "/py/bin/python" in plist
    assert "<string>/wd</string>" in plist  # WorkingDirectory
    assert "/log/memory-review.log" in plist
    assert "/log/memory-review.err" in plist
    assert "<key>PATH</key>" in plist  # explicit PATH env


def test_render_plist_calendar_interval() -> None:
    plist = schedule.render_plist(
        "03:17", interpreter="/p", workdir="/w", log_dir="/l"
    )
    assert "<key>Hour</key><integer>3</integer>" in plist
    assert "<key>Minute</key><integer>17</integer>" in plist


def test_install_writes_plist_under_home(tmp_path: Any) -> None:
    path = schedule.install("02:30", home=tmp_path, load=False)
    assert path.exists()
    assert path.parent == tmp_path / "Library" / "LaunchAgents"
    body = path.read_text(encoding="utf-8")
    assert "cn.trowel.memory-review" in body
    assert "<string>memory</string>" in body  # the review subcommand
    assert "<string>review</string>" in body


def test_status_installed_not_loaded(tmp_path: Any) -> None:
    schedule.install("02:30", home=tmp_path, load=False)
    installed, loaded = schedule.status(home=tmp_path)
    assert installed is True
    assert loaded is False  # subprocess stubbed to returncode=1


def test_status_not_installed(tmp_path: Any) -> None:
    installed, loaded = schedule.status(home=tmp_path)
    assert (installed, loaded) == (False, False)


def test_uninstall_removes_plist(tmp_path: Any) -> None:
    schedule.install("02:30", home=tmp_path, load=False)
    removed = schedule.uninstall(home=tmp_path)
    assert removed is True
    assert not schedule._plist_path(tmp_path).exists()  # noqa: SLF001


def test_uninstall_idempotent_when_absent(tmp_path: Any) -> None:
    removed = schedule.uninstall(home=tmp_path)
    assert removed is False  # nothing to remove, no crash


def test_parse_hhmm_validates_boundary() -> None:
    """_parse_hhmm rejects garbage at the boundary (CR M-3)."""
    with pytest.raises(ValueError):
        schedule._parse_hhmm("")  # noqa: SLF001
    with pytest.raises(ValueError):
        schedule._parse_hhmm("25:70")  # noqa: SLF001 — out of range
    with pytest.raises(ValueError):
        schedule._parse_hhmm("2:30:45")  # noqa: SLF001 — too many parts
    with pytest.raises(ValueError):
        schedule._parse_hhmm("abc")  # noqa: SLF001 — non-integer
    assert schedule._parse_hhmm("02:30") == (2, 30)  # noqa: SLF001
    assert schedule._parse_hhmm("23:59") == (23, 59)  # noqa: SLF001
