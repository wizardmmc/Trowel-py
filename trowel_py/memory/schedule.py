"""launchd scheduling for the daily memory review (slice-040-b C-3/C-4).

Provides a versioned launchd plist template plus ``install`` / ``status`` /
``uninstall`` / ``run_now``. The job runs ``trowel-py memory review`` WITHOUT
``--date``: the command reads the completed/extracted water marks and processes
every pending incremental slice, so a missed or sleep-shifted run catches up
instead of permanently skipping a day (C-4).

All functions take ``home=`` (default ``Path.home()``) so tests point at a tmp
HOME and never touch the real ``~/Library/LaunchAgents``. ``launchctl`` calls are
guarded by ``sys.platform == "darwin"`` and use ``check=False`` (a scheduling
subsystem failure must never crash the memory tooling); tests monkeypatch
``subprocess.run`` to avoid the real launchctl.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

LABEL = "cn.trowel.memory-review"

#: the trowel repo root (this file is trowel_py/memory/schedule.py). The plist's
#: WorkingDirectory + log paths resolve under here so the job runs the same code
#: the user invoked ``install`` from.
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _parse_hhmm(time: str) -> tuple[int, int]:
    """Validate an ``HH:MM`` string → ``(hour, minute)``; raise ``ValueError``.

    Rejects garbage like ``""``, ``"25:70"``, ``"2:30:45"`` at the boundary
    rather than letting launchd silently mis-schedule.
    """
    parts = time.split(":")
    if len(parts) != 2:
        raise ValueError(f"expected HH:MM, got {time!r}")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError(f"non-integer HH or MM in {time!r}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"HH out of [0,23] or MM out of [0,59]: {time!r}")
    return hour, minute


def _plist_path(home: Path | None = None) -> Path:
    """Resolve the plist path under ``home``'s LaunchAgents (default Path.home())."""
    h = home or Path.home()
    return h / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def render_plist(
    time: str = "02:30",
    *,
    interpreter: str | None = None,
    workdir: str | None = None,
    log_dir: str | None = None,
) -> str:
    """Render the launchd plist for the daily review job (versioned template).

    Args:
        time: ``HH:MM`` start time (default 02:30 — late night, low GLM load).
        interpreter: absolute python interpreter (default ``sys.executable``).
        workdir: WorkingDirectory (default the repo root).
        log_dir: StandardOut/Err dir (default ``<repo>/logs``).

    Returns:
        The plist XML text. Absolute interpreter + WorkingDirectory + explicit
        PATH so the job does not depend on the launchd-minimal default env.
    """
    hour, minute = _parse_hhmm(time)
    py = interpreter or sys.executable
    wd = workdir or str(_REPO_ROOT)
    ld = log_dir or str(_REPO_ROOT / "logs")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{py}</string>
        <string>-m</string>
        <string>trowel_py.cli</string>
        <string>memory</string>
        <string>review</string>
    </array>
    <key>WorkingDirectory</key><string>{wd}</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>{hour}</integer>
        <key>Minute</key><integer>{minute}</integer>
    </dict>
    <key>StandardOutPath</key><string>{ld}/memory-review.log</string>
    <key>StandardErrorPath</key><string>{ld}/memory-review.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
"""


def install(
    time: str = "02:30", home: Path | None = None, *, load: bool = True
) -> Path:
    """Write the plist (and ``launchctl load`` it on Darwin unless ``load`` is False).

    Returns the written plist path. Tests pass ``load=False`` and ``home=tmp`` so
    nothing touches the real LaunchAgents or the real launchctl.
    """
    path = _plist_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_plist(time), encoding="utf-8")
    if load and sys.platform == "darwin":
        subprocess.run(["launchctl", "load", str(path)], check=False)
    return path


def status(home: Path | None = None) -> tuple[bool, bool]:
    """Return ``(installed, loaded)``. ``loaded`` is False off-Darwin."""
    path = _plist_path(home)
    installed = path.exists()
    if not installed:
        return (False, False)
    if sys.platform != "darwin":
        return (True, False)
    res = subprocess.run(["launchctl", "list", LABEL], capture_output=True, check=False)
    return (True, res.returncode == 0)


def uninstall(home: Path | None = None) -> bool:
    """``launchctl unload`` (Darwin) + remove the plist. Returns True if removed."""
    path = _plist_path(home)
    if sys.platform == "darwin" and path.exists():
        subprocess.run(["launchctl", "unload", str(path)], check=False)
    if path.exists():
        path.unlink()
        return True
    return False


def run_now(home: Path | None = None) -> bool:
    """Trigger the loaded job immediately via ``launchctl start``.

    Returns False off-Darwin or if launchctl reports failure (``check=False`` —
    the caller logs, never crashes). Mostly a convenience for manual verify.
    """
    # `home` is accepted for API symmetry; launchctl targets the label, not a path.
    _ = home
    if sys.platform != "darwin":
        return False
    res = subprocess.run(["launchctl", "start", LABEL], check=False)
    return res.returncode == 0
