from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import trowel_py.memory.profile_suggestions as profile_suggestions
from tests.memory.profile_suggestions.support import suggestion
from trowel_py.memory.profile_suggestions import (
    append_suggestions,
    load_suggestions,
)


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_suggestions(tmp_path) == []


def test_load_empty_suggestions_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "profile-suggestions.json").write_text(
        json.dumps({"suggestions": [], "updated": "2026-07-15"}),
        encoding="utf-8",
    )
    assert load_suggestions(tmp_path) == []


def test_load_corrupt_json_raises(tmp_path: Path) -> None:
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "profile-suggestions.json").write_text(
        "{not valid json",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_suggestions(tmp_path)


def test_append_then_load_roundtrips(tmp_path: Path) -> None:
    items = [
        suggestion(id_="s1"),
        suggestion(id_="s2", dimension="goal", body="目标样例"),
    ]
    append_suggestions(tmp_path, items, updated="2026-07-15")
    loaded = load_suggestions(tmp_path)
    assert loaded == items
    assert loaded[0].sources == ("2026-07-14 cc_session_abc",)
    assert isinstance(loaded[0].sources, tuple)


def test_append_accumulates_across_writes(tmp_path: Path) -> None:
    append_suggestions(
        tmp_path,
        [suggestion(id_="s1")],
        updated="2026-07-15",
    )
    append_suggestions(
        tmp_path,
        [suggestion(id_="s2", body="second")],
        updated="2026-07-16",
    )
    assert [item.id for item in load_suggestions(tmp_path)] == ["s1", "s2"]
    data = json.loads(
        (tmp_path / "meta" / "profile-suggestions.json").read_text(encoding="utf-8")
    )
    assert data["updated"] == "2026-07-16"


def test_append_empty_list_is_harmless(tmp_path: Path) -> None:
    append_suggestions(tmp_path, [], updated="2026-07-15")
    assert load_suggestions(tmp_path) == []


def test_new_suggestion_writes_policy_version_2(tmp_path: Path) -> None:
    append_suggestions(
        tmp_path,
        [suggestion(id_="s1")],
        updated="2026-07-15",
    )
    data = json.loads(
        (tmp_path / "meta" / "profile-suggestions.json").read_text(encoding="utf-8")
    )
    assert data["suggestions"][0]["policy_version"] == (
        profile_suggestions.PROFILE_DISTILL_POLICY_VERSION
    )


def test_facade_storage_constants_flow_to_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(profile_suggestions, "_META_DIR", "queue")
    monkeypatch.setattr(
        profile_suggestions,
        "_SUGGESTIONS_FILE",
        "suggestions.json",
    )
    append_suggestions(tmp_path, [], updated="2026-07-15")
    assert (tmp_path / "queue" / "suggestions.json").is_file()


def test_queue_json_bytes_remain_stable(tmp_path: Path) -> None:
    append_suggestions(
        tmp_path,
        [
            suggestion(
                id_="s1",
                body="示例",
                sources=("session-a",),
                date="2026-01-02",
            )
        ],
        updated="2026-01-03",
    )
    payload = (tmp_path / "meta" / "profile-suggestions.json").read_bytes()
    assert hashlib.sha256(payload).hexdigest() == (
        "eef4f0b1f7bbdf33fae9bfa00d9629bc5d05c096deb0652be7857ea81c14c356"
    )
    assert not payload.endswith(b"\n")


def test_empty_append_updates_stamp(tmp_path: Path) -> None:
    append_suggestions(tmp_path, [], updated="2026-07-15")
    append_suggestions(tmp_path, [], updated="2026-07-16")
    data = json.loads(
        (tmp_path / "meta" / "profile-suggestions.json").read_text(encoding="utf-8")
    )
    assert data == {"suggestions": [], "updated": "2026-07-16"}
