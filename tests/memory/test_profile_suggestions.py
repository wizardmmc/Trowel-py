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
) -> Suggestion:
    return Suggestion(
        id=id_,
        dimension=dimension,  # type: ignore[arg-type]
        body=body,
        sources=sources,
        date=date,
        status=status,  # type: ignore[arg-type]
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
