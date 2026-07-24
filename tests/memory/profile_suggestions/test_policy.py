from __future__ import annotations

from pathlib import Path

import pytest

import trowel_py.memory.profile_suggestions as profile_suggestions
from tests.memory.profile_suggestions.support import (
    V1_SHAPE,
    suggestion,
    write_raw_queue,
)
from trowel_py.memory.profile_suggestions import (
    PROFILE_DISTILL_POLICY_VERSION,
    append_suggestions,
    load_suggestions,
    pending_suggestions,
    update_suggestion_status,
)


def test_update_status_to_accepted(tmp_path: Path) -> None:
    append_suggestions(
        tmp_path,
        [suggestion(id_="s1")],
        updated="2026-07-15",
    )
    update_suggestion_status(tmp_path, "s1", "accepted")
    [loaded] = load_suggestions(tmp_path)
    assert loaded.status == "accepted"


def test_update_status_to_discarded(tmp_path: Path) -> None:
    append_suggestions(
        tmp_path,
        [suggestion(id_="s1")],
        updated="2026-07-15",
    )
    update_suggestion_status(tmp_path, "s1", "discarded")
    assert load_suggestions(tmp_path)[0].status == "discarded"


def test_update_status_unknown_id_raises(tmp_path: Path) -> None:
    append_suggestions(
        tmp_path,
        [suggestion(id_="s1")],
        updated="2026-07-15",
    )
    with pytest.raises(KeyError):
        update_suggestion_status(tmp_path, "nope", "accepted")


def test_update_status_bad_value_raises(tmp_path: Path) -> None:
    append_suggestions(
        tmp_path,
        [suggestion(id_="s1")],
        updated="2026-07-15",
    )
    with pytest.raises(ValueError):
        update_suggestion_status(tmp_path, "s1", "bogus")  # type: ignore[arg-type]


def test_pending_filters_out_resolved(tmp_path: Path) -> None:
    append_suggestions(
        tmp_path,
        [
            suggestion(id_="s1", status="pending"),
            suggestion(id_="s2", status="accepted"),
            suggestion(id_="s3", status="discarded"),
            suggestion(id_="s4", status="pending", body="another"),
        ],
        updated="2026-07-15",
    )
    assert [item.id for item in pending_suggestions(tmp_path)] == ["s1", "s4"]


def test_pending_empty_when_all_resolved(tmp_path: Path) -> None:
    append_suggestions(
        tmp_path,
        [suggestion(id_="s1", status="accepted")],
        updated="2026-07-15",
    )
    assert pending_suggestions(tmp_path) == []


def test_pending_filters_out_v1_by_default(tmp_path: Path) -> None:
    write_raw_queue(
        tmp_path,
        [
            V1_SHAPE,
            {
                "id": "v2-1",
                "dimension": "expression",
                "body": "简洁表达",
                "sources": ["session-new"],
                "date": "2026-07-17",
                "status": "pending",
                "policy_version": PROFILE_DISTILL_POLICY_VERSION,
            },
        ],
    )
    assert [item.id for item in pending_suggestions(tmp_path)] == ["v2-1"]


def test_pending_v1_records_unchanged_on_disk(tmp_path: Path) -> None:
    payload = write_raw_queue(tmp_path, [V1_SHAPE])
    pending_suggestions(tmp_path)
    assert (tmp_path / "meta" / "profile-suggestions.json").read_text(
        encoding="utf-8"
    ) == payload
    assert [item.id for item in load_suggestions(tmp_path)] == ["v1-1"]


def test_pending_policy_none_returns_all_versions(tmp_path: Path) -> None:
    write_raw_queue(
        tmp_path,
        [
            V1_SHAPE,
            {
                "id": "v2-1",
                "dimension": "expression",
                "body": "简洁表达",
                "sources": ["session-new"],
                "date": "2026-07-17",
                "status": "pending",
                "policy_version": PROFILE_DISTILL_POLICY_VERSION,
            },
        ],
    )
    pending = pending_suggestions(tmp_path, current_policy_version=None)
    assert {item.id for item in pending} == {"v1-1", "v2-1"}


def test_facade_load_patch_flows_to_public_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = suggestion(id_="patched")
    monkeypatch.setattr(
        profile_suggestions,
        "_load_queue",
        lambda _root: ([item], "2026-07-15"),
    )
    assert load_suggestions(tmp_path) == [item]
    assert pending_suggestions(tmp_path) == [item]


def test_status_update_preserves_order_stamp_and_updates_duplicate_ids(
    tmp_path: Path,
) -> None:
    append_suggestions(
        tmp_path,
        [
            suggestion(id_="same", body="first"),
            suggestion(id_="other"),
            suggestion(id_="same", body="last"),
        ],
        updated="2026-07-15",
    )
    update_suggestion_status(tmp_path, "same", "accepted")
    loaded = load_suggestions(tmp_path)
    assert [(item.id, item.body, item.status) for item in loaded] == [
        ("same", "first", "accepted"),
        ("other", "能够维护服务接口", "pending"),
        ("same", "last", "accepted"),
    ]
    raw = (tmp_path / "meta" / "profile-suggestions.json").read_text(encoding="utf-8")
    assert '"updated": "2026-07-15"' in raw


@pytest.mark.parametrize(
    ("suggestion_id", "status", "exception"),
    [
        ("missing", "accepted", KeyError),
        ("s1", "bogus", ValueError),
    ],
)
def test_rejected_status_updates_leave_file_unchanged(
    tmp_path: Path,
    suggestion_id: str,
    status: str,
    exception: type[Exception],
) -> None:
    append_suggestions(
        tmp_path,
        [suggestion(id_="s1")],
        updated="2026-07-15",
    )
    path = tmp_path / "meta" / "profile-suggestions.json"
    before = path.read_bytes()
    with pytest.raises(exception):
        update_suggestion_status(  # type: ignore[arg-type]
            tmp_path,
            suggestion_id,
            status,
        )
    assert path.read_bytes() == before
