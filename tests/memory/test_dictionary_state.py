"""dictionary state file (slice-064 §5): success hash + staleness, atomic."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trowel_py.memory.dictionary_state import (
    DictionaryState,
    load_state,
    save_state,
    state_path,
)


def test_load_missing_file_is_missing(tmp_path: Path) -> None:
    state = load_state(tmp_path)
    assert state.status == "missing"
    assert state.source_hash is None
    assert state.last_success_at is None


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    s = DictionaryState(
        status="consistent",
        source_hash="abc123",
        last_success_at="2026-07-18T10:00:00",
    )
    save_state(tmp_path, s)
    assert state_path(tmp_path).exists()
    again = load_state(tmp_path)
    assert again == s


def test_load_corrupt_file_bootstraps_to_missing(tmp_path: Path) -> None:
    # a corrupt file must NEVER read as "consistent" (C-7): degrade to missing
    # so the next run re-checks + rebuilds instead of trusting a stale index.
    state_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    state_path(tmp_path).write_text("{not valid json", encoding="utf-8")
    state = load_state(tmp_path)
    assert state.status == "missing"
    assert state.source_hash is None


def test_save_is_atomic_via_temp_replace(tmp_path: Path) -> None:
    # crash mid-write never leaves a half file (C-6): temp then os.replace.
    s = DictionaryState(status="consistent", source_hash="h")
    save_state(tmp_path, s)
    payload = json.loads(state_path(tmp_path).read_text(encoding="utf-8"))
    assert payload["status"] == "consistent"
    assert payload["source_hash"] == "h"
    # no stray temp file left behind
    assert not state_path(tmp_path).with_name(
        state_path(tmp_path).name + ".tmp"
    ).exists()


def test_immutable_with_helpers(tmp_path: Path) -> None:
    s = DictionaryState()
    s2 = s.with_success("hash1", "rendered1", "2026-07-18T10:00:00")
    assert s.status == "missing"  # original untouched
    assert s2.status == "consistent"
    assert s2.source_hash == "hash1"
    assert s2.rendered_hash == "rendered1"
    s3 = s2.with_failure("provider 529", "2026-07-18T11:00:00")
    assert s3.status == "stale"
    assert s3.source_hash == "hash1"  # success hash preserved across failure
    assert s3.last_failure_reason == "provider 529"


def test_save_failure_leaves_prior_state_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C-6 atomicity: if the atomic replace fails mid-save, the prior state
    file is untouched (not half-written) — a direct write would not guarantee
    this."""
    save_state(tmp_path, DictionaryState().with_success("h1", "r1", "t1"))
    prior_text = state_path(tmp_path).read_text(encoding="utf-8")

    import trowel_py.memory.dictionary_state as dsmod

    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise OSError("injected at os.replace")

    monkeypatch.setattr(dsmod.os, "replace", boom)
    with pytest.raises(OSError):
        save_state(tmp_path, DictionaryState().with_success("h2", "r2", "t2"))

    # prior file byte-for-byte intact
    assert state_path(tmp_path).read_text(encoding="utf-8") == prior_text
    assert load_state(tmp_path).source_hash == "h1"
