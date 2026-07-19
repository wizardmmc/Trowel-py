"""Env-gated raw protocol recorder.

Spec §4: raw recording is off by default and only enabled by an explicit
environment variable. Every line is run through :func:`redact_message` before
it touches disk so auth, tokens and credential-bearing proxy strings can never
leak into a fixture (C-6).

The recorder writes JSONL lines of ``{"t": <epoch>, "dir": "in"|"out", "msg":
<redacted>}``. It is intentionally a small, dependency-free class: the
transport owns the lifecycle, the recorder only owns the file.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, BinaryIO

from trowel_py.codex_host.secrets import redact_message

# Opt-in switch. Unset or empty → recording disabled everywhere.
RECORDER_ENV_FLAG = "TROWEL_CODEX_RECORD"


def recording_enabled(target: Path | str) -> bool:
    """Return True if recording is explicitly enabled for the given target.

    A bare ``TROWEL_CODEX_RECORD=1`` enables recording to any path. A path
    value (``TROWEL_CODEX_RECORD=/tmp/x.jsonl``) enables it only when the
    requested target matches — this lets a test enable one recorder without
    accidentally turning on a neighbour's.

    Args:
        target: The file the caller intends to write to.

    Returns:
        True if the environment variable sanctions recording here.
    """

    flag = os.environ.get(RECORDER_ENV_FLAG, "").strip()
    if not flag:
        return False
    if flag in {"1", "true", "True", "yes"}:
        return True
    return Path(flag) == Path(target)


class RawRecorder:
    """Append redacted protocol lines to a JSONL file when enabled.

    The file is opened lazily on the first write and closed via :meth:`close`.
    Disabled recorders are no-ops — every method is safe to call regardless of
    the flag, so the transport does not need its own ``if recording`` guards.
    """

    def __init__(self, path: Path, *, clock: Any = time.time) -> None:
        """Store the target path and clock; do not open the file yet.

        Args:
            path: JSONL file to append to.
            clock: Injectable now-callable (epoch seconds) for deterministic
                timestamps in tests.
        """

        self._path = path
        self._clock = clock
        self._handle: BinaryIO | None = None
        self._enabled = recording_enabled(path)

    @property
    def enabled(self) -> bool:
        """True if this recorder will actually write to disk."""

        return self._enabled

    def record(self, direction: str, message: Any) -> None:
        """Append one redacted message if enabled; otherwise do nothing.

        Args:
            direction: ``"out"`` for client→server, ``"in"`` for server→client.
            message: The parsed JSON message (redacted before write).
        """

        if not self._enabled:
            return
        if self._handle is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self._path.open("ab")
        record = {
            "t": self._clock(),
            "dir": direction,
            "msg": redact_message(message),
        }
        self._handle.write((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
        self._handle.flush()

    def close(self) -> None:
        """Close the file handle if one was opened. Idempotent."""

        if self._handle is not None:
            self._handle.close()
            self._handle = None
