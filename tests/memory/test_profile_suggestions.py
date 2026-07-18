"""slice-050 profile suggestion queue IO tests (TDD RED → GREEN).

Covers the candidate-queue invariants from docs/slices/activate/slice-050.md:
append/load round-trip (incl. sources tuple + date + status), accumulation,
status transitions (accept/discard), pending filter, and degraded loading
(missing/empty file → []; corrupt JSON → loud ValueError, never silent).
All via tmp_path fixtures — no real memory DB.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trowel_py.memory.profile_suggestions import (
    PROFILE_DISTILL_POLICY_VERSION,
    append_suggestions,
    load_suggestions,
    pending_suggestions,
    update_suggestion_status,
)
from trowel_py.memory.types import Suggestion


def _sug(
    id_: str = "s1",
    dimension: str = "ability",
    body: str = "全栈：Python FastAPI + React",
    sources: tuple[str, ...] = ("2026-07-14 cc_session_abc",),
    date: str = "2026-07-14",
    status: str = "pending",
    policy_version: int = PROFILE_DISTILL_POLICY_VERSION,
) -> Suggestion:
    return Suggestion(
        id=id_,
        dimension=dimension,  # type: ignore[arg-type]
        body=body,
        sources=sources,
        date=date,
        status=status,  # type: ignore[arg-type]
        policy_version=policy_version,
    )


# ---------- load: degraded cases ----------


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    # cold start: no profile-suggestions.json yet → []
    assert load_suggestions(tmp_path) == []


def test_load_empty_suggestions_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "profile-suggestions.json").write_text(
        json.dumps({"suggestions": [], "updated": "2026-07-15"}),
        encoding="utf-8",
    )
    assert load_suggestions(tmp_path) == []


def test_load_corrupt_json_raises(tmp_path: Path) -> None:
    # C-6: corrupt data must NOT silently degrade to [] — raise so the caller
    # knows the queue is damaged (distinct from "no data yet").
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "profile-suggestions.json").write_text(
        "{not valid json", encoding="utf-8"
    )
    with pytest.raises(ValueError):
        load_suggestions(tmp_path)


# ---------- append + load round-trip ----------


def test_append_then_load_roundtrips(tmp_path: Path) -> None:
    items = [_sug(id_="s1"), _sug(id_="s2", dimension="goal", body="求职字节")]
    append_suggestions(tmp_path, items, updated="2026-07-15")
    loaded = load_suggestions(tmp_path)
    assert loaded == items
    # sources survive as a tuple (not a list) — value object stays immutable
    assert loaded[0].sources == ("2026-07-14 cc_session_abc",)
    assert isinstance(loaded[0].sources, tuple)


def test_append_accumulates_across_writes(tmp_path: Path) -> None:
    append_suggestions(tmp_path, [_sug(id_="s1")], updated="2026-07-15")
    append_suggestions(tmp_path, [_sug(id_="s2", body="second")], updated="2026-07-16")
    loaded = load_suggestions(tmp_path)
    assert [s.id for s in loaded] == ["s1", "s2"]
    # the queue's `updated` stamp tracks the latest append
    data = json.loads(
        (tmp_path / "meta" / "profile-suggestions.json").read_text(encoding="utf-8")
    )
    assert data["updated"] == "2026-07-16"


def test_append_empty_list_is_harmless(tmp_path: Path) -> None:
    # a distill run that produces no new suggestions still updates the stamp
    # without corrupting the queue
    append_suggestions(tmp_path, [], updated="2026-07-15")
    assert load_suggestions(tmp_path) == []


def test_status_defaults_to_pending(tmp_path: Path) -> None:
    append_suggestions(tmp_path, [_sug(status="pending")], updated="2026-07-15")
    [loaded] = load_suggestions(tmp_path)
    assert loaded.status == "pending"


# ---------- status transitions ----------


def test_update_status_to_accepted(tmp_path: Path) -> None:
    append_suggestions(tmp_path, [_sug(id_="s1")], updated="2026-07-15")
    update_suggestion_status(tmp_path, "s1", "accepted")
    [loaded] = load_suggestions(tmp_path)
    assert loaded.status == "accepted"


def test_update_status_to_discarded(tmp_path: Path) -> None:
    append_suggestions(tmp_path, [_sug(id_="s1")], updated="2026-07-15")
    update_suggestion_status(tmp_path, "s1", "discarded")
    assert load_suggestions(tmp_path)[0].status == "discarded"


def test_update_status_unknown_id_raises(tmp_path: Path) -> None:
    append_suggestions(tmp_path, [_sug(id_="s1")], updated="2026-07-15")
    with pytest.raises(KeyError):
        update_suggestion_status(tmp_path, "nope", "accepted")


def test_update_status_bad_value_raises(tmp_path: Path) -> None:
    append_suggestions(tmp_path, [_sug(id_="s1")], updated="2026-07-15")
    with pytest.raises(ValueError):
        update_suggestion_status(tmp_path, "s1", "bogus")  # type: ignore[arg-type]


# ---------- pending filter ----------


def test_pending_filters_out_resolved(tmp_path: Path) -> None:
    append_suggestions(
        tmp_path,
        [
            _sug(id_="s1", status="pending"),
            _sug(id_="s2", status="accepted"),
            _sug(id_="s3", status="discarded"),
            _sug(id_="s4", status="pending", body="another"),
        ],
        updated="2026-07-15",
    )
    pending = pending_suggestions(tmp_path)
    assert [s.id for s in pending] == ["s1", "s4"]


def test_pending_empty_when_all_resolved(tmp_path: Path) -> None:
    append_suggestions(
        tmp_path, [_sug(id_="s1", status="accepted")], updated="2026-07-15"
    )
    assert pending_suggestions(tmp_path) == []


# ---------- corrupt-on-disk: bad status value ----------


def test_load_bad_status_value_raises(tmp_path: Path) -> None:
    # a hand-edited queue with an unknown status is data corruption → raise,
    # not silently coerce (keeps the lifecycle enumerable + auditable).
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "profile-suggestions.json").write_text(
        json.dumps(
            {
                "suggestions": [
                    {
                        "id": "s1",
                        "dimension": "ability",
                        "body": "x",
                        "sources": [],
                        "date": "2026-07-14",
                        "status": "frozen",
                    }
                ],
                "updated": "2026-07-15",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_suggestions(tmp_path)


# ---------- slice-067: policy_version compat + filtering ----------


def _write_raw_queue(tmp_path: Path, suggestions: list) -> str:
    """Hand-write a queue file verbatim (simulates a v1 on-disk shape)."""
    (tmp_path / "meta").mkdir(exist_ok=True)
    payload = json.dumps(
        {"suggestions": suggestions, "updated": "2026-07-15"}, ensure_ascii=False
    )
    (tmp_path / "meta" / "profile-suggestions.json").write_text(payload, encoding="utf-8")
    return payload


_V1_SHAPE = {
    "id": "v1-1",
    "dimension": "ability",
    "body": "一条很长的 v1 能力描述，把例子论证评价都塞进同一条 body",
    "sources": ["cc-sess-old"],
    "date": "2026-07-10",
    "status": "pending",
}  # note: NO policy_version field — a real v1 record on disk


def test_v1_record_without_policy_version_loads_as_1(tmp_path: Path) -> None:
    # C-6 兼容: a v1 record missing the field reads as policy_version=1
    _write_raw_queue(tmp_path, [_V1_SHAPE])
    [loaded] = load_suggestions(tmp_path)
    assert loaded.policy_version == 1


def test_v1_record_on_disk_not_rewritten(tmp_path: Path) -> None:
    # C-6: reading must NOT batch-rewrite old records in place
    payload = _write_raw_queue(tmp_path, [_V1_SHAPE])
    load_suggestions(tmp_path)
    assert (tmp_path / "meta" / "profile-suggestions.json").read_text(
        encoding="utf-8"
    ) == payload


def test_new_suggestion_writes_policy_version_2(tmp_path: Path) -> None:
    append_suggestions(tmp_path, [_sug(id_="s1")], updated="2026-07-15")
    data = json.loads(
        (tmp_path / "meta" / "profile-suggestions.json").read_text(encoding="utf-8")
    )
    assert data["suggestions"][0]["policy_version"] == PROFILE_DISTILL_POLICY_VERSION


def test_pending_filters_out_v1_by_default(tmp_path: Path) -> None:
    # the default-aged GET pending API surfaces only the current policy's pending
    _write_raw_queue(
        tmp_path,
        [
            _V1_SHAPE,  # v1 pending — must NOT surface by default
            {
                "id": "v2-1",
                "dimension": "expression",
                "body": "避免 AI 味",
                "sources": ["cc-sess-new"],
                "date": "2026-07-17",
                "status": "pending",
                "policy_version": PROFILE_DISTILL_POLICY_VERSION,
            },
        ],
    )
    pending = pending_suggestions(tmp_path)
    assert [s.id for s in pending] == ["v2-1"]


def test_pending_v1_records_unchanged_on_disk(tmp_path: Path) -> None:
    # surfacing v2 pending must not delete / coerce the v1 records (audit)
    payload = _write_raw_queue(tmp_path, [_V1_SHAPE])
    pending_suggestions(tmp_path)  # default: current policy only
    assert (tmp_path / "meta" / "profile-suggestions.json").read_text(
        encoding="utf-8"
    ) == payload
    # and load_suggestions still returns the v1 record verbatim
    assert [s.id for s in load_suggestions(tmp_path)] == ["v1-1"]


def test_pending_policy_none_returns_all_versions(tmp_path: Path) -> None:
    # audit / recalibration tooling passes None to see every pending item
    _write_raw_queue(
        tmp_path,
        [
            _V1_SHAPE,
            {
                "id": "v2-1",
                "dimension": "expression",
                "body": "避免 AI 味",
                "sources": ["cc-sess-new"],
                "date": "2026-07-17",
                "status": "pending",
                "policy_version": PROFILE_DISTILL_POLICY_VERSION,
            },
        ],
    )
    pending = pending_suggestions(tmp_path, current_policy_version=None)
    assert {s.id for s in pending} == {"v1-1", "v2-1"}
