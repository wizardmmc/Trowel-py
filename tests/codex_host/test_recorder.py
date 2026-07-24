from __future__ import annotations

import json
from pathlib import Path

from trowel_py.codex_host.recorder import (
    RECORDER_ENV_FLAG,
    RawRecorder,
    recording_enabled,
)


def test_recording_disabled_by_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv(RECORDER_ENV_FLAG, raising=False)
    path = tmp_path / "protocol.jsonl"
    rec = RawRecorder(path)
    assert rec.enabled is False
    rec.record("out", {"id": 1, "method": "initialize", "params": {"token": "x"}})
    rec.close()
    assert not path.exists()


def test_recording_enabled_by_boolean_flag(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(RECORDER_ENV_FLAG, "1")
    rec = RawRecorder(tmp_path / "a.jsonl")
    assert rec.enabled is True


def test_recording_scoped_to_specific_path(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "chosen.jsonl"
    monkeypatch.setenv(RECORDER_ENV_FLAG, str(target))
    assert recording_enabled(target) is True
    assert recording_enabled(tmp_path / "other.jsonl") is False


def test_record_redacts_before_write(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(RECORDER_ENV_FLAG, "1")
    path = tmp_path / "protocol.jsonl"
    rec = RawRecorder(path, clock=lambda: 1000.0)
    rec.record(
        "in",
        {
            "method": "item/completed",
            "params": {"auth": {"bearer": "Bearer sk-1234567890abcdef"}},
        },
    )
    rec.close()
    line = json.loads(path.read_text().strip())
    assert line["t"] == 1000.0
    assert line["dir"] == "in"
    assert "sk-1234567890abcdef" not in path.read_text()
    assert "Bearer ***" in path.read_text() or "***REDACTED***" in path.read_text()


def test_record_is_idempotent_close(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(RECORDER_ENV_FLAG, "1")
    rec = RawRecorder(tmp_path / "p.jsonl")
    rec.close()
    rec.close()
