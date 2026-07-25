from __future__ import annotations

import base64
import json
from collections.abc import Callable

import pytest

from trowel_py.model_os.journal import (
    InvalidJournalCursor,
    JournalFilter,
)
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os import journal
from trowel_py.model_os.types import (
    DecisionDisposition,
    DecisionRecord,
    EventEnvelope,
    EventKind,
    Provenance,
)


def _event(index: int, recorded_at: str, *, kind: str = EventKind.NOTE) -> EventEnvelope:
    return EventEnvelope(
        event_id=f"event.{index}",
        kind=kind,
        occurred_at=recorded_at,
        source="test",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v1",
        payload={"index": index},
        task_id="task.odd" if index % 2 else "task.even",
        correlation_id=f"correlation.{index}",
        outcome="observed",
    )


def _decision(index: int, recorded_at: str) -> DecisionRecord:
    return DecisionRecord(
        decision_id=f"decision.{index}",
        kind="route",
        disposition=DecisionDisposition.NO_ACTION,
        decided_at=recorded_at,
        signals={"refs": []},
        candidates=["fast", "deep"],
        choice="fast",
        reason="default_fast",
        policy_version="v1",
        task_id="task.odd" if index % 2 else "task.even",
    )


def _seed(store: ModelOsStore, count: int) -> None:
    for index in range(count):
        recorded_at = f"2026-07-25T00:{(index * 17) % 60:02d}:00Z"
        if index % 3:
            store.append_event(_event(index, recorded_at))
        else:
            store.append_decision(_decision(index, recorded_at))


def _walk(
    store: ModelOsStore,
    limit: int,
    *,
    journal_filter: JournalFilter | None = None,
) -> tuple[list[tuple[str, int]], object]:
    cursor = None
    seen: list[tuple[str, int]] = []
    boundary = None
    while True:
        page = store.read_journal_page(
            journal_filter=journal_filter,
            limit=limit,
            cursor=cursor,
        )
        boundary = boundary or page.as_of
        assert page.as_of == boundary
        seen.extend((item.stream, item.stream_seq) for item in page.items)
        cursor = page.next_cursor
        if cursor is None:
            return seen, boundary


@pytest.mark.parametrize("limit", [1, 7, 50, 100, 500])
def test_pages_traverse_fixed_boundary_without_duplicates_or_gaps(
    store: ModelOsStore, limit: int
) -> None:
    _seed(store, 630)
    seen, boundary = _walk(store, limit)

    assert len(seen) == 630
    assert len(set(seen)) == 630
    assert boundary.event_seq + boundary.decision_seq == 630


def test_same_time_orders_decisions_before_events(store: ModelOsStore) -> None:
    at = "2026-07-25T00:00:00Z"
    store.append_event(_event(1, at))
    store.append_decision(_decision(2, at))
    store.append_event(_event(3, at))
    store.append_decision(_decision(4, at))

    page = store.read_journal_page(limit=10)

    assert [item.stream for item in page.items] == [
        "decision",
        "decision",
        "event",
        "event",
    ]


def test_backdated_and_concurrent_appends_wait_for_fresh_query(
    store: ModelOsStore,
) -> None:
    _seed(store, 20)
    first = store.read_journal_page(limit=3)
    store.append_event(_event(100, "2000-01-01T00:00:00Z"))
    store.append_decision(_decision(101, "2000-01-01T00:00:00Z"))

    cursor = first.next_cursor
    fixed = [(item.stream, item.entry_id) for item in first.items]
    while cursor is not None:
        page = store.read_journal_page(limit=3, cursor=cursor)
        assert page.as_of == first.as_of
        fixed.extend((item.stream, item.entry_id) for item in page.items)
        cursor = page.next_cursor

    assert len(fixed) == 20
    assert all(entry_id not in {"event.100", "decision.101"} for _, entry_id in fixed)
    fresh, _ = _walk(store, 7)
    assert len(fresh) == 22


def _tamper_cursor(cursor: str, mutate: Callable[[dict[str, object]], None]) -> str:
    padded = cursor + "=" * (-len(cursor) % 4)
    body = json.loads(base64.urlsafe_b64decode(padded))
    mutate(body)
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def test_cursor_damage_version_filter_and_limit_are_rejected(
    store: ModelOsStore,
) -> None:
    _seed(store, 5)
    cursor = store.read_journal_page(limit=1).next_cursor
    assert cursor is not None

    for broken in (
        cursor[:-2] + "xx",
        _tamper_cursor(cursor, lambda body: body.__setitem__("v", 99)),
    ):
        with pytest.raises(InvalidJournalCursor):
            store.read_journal_page(limit=1, cursor=broken)
    with pytest.raises(InvalidJournalCursor):
        store.read_journal_page(
            journal_filter=JournalFilter(task_id="task.odd"),
            limit=1,
            cursor=cursor,
        )
    for limit in (0, 501):
        with pytest.raises(ValueError):
            store.read_journal_page(limit=limit)


def test_filter_is_applied_to_both_streams(store: ModelOsStore) -> None:
    _seed(store, 30)
    page = store.read_journal_page(
        journal_filter=JournalFilter(task_id="task.odd"),
        limit=100,
    )

    assert len(page.items) == 15
    assert all(item.task_id == "task.odd" for item in page.items)
    assert all(not hasattr(item, "payload") for item in page.items)
    assert all(not hasattr(item, "signals") for item in page.items)


def test_single_page_materializes_bounded_rows_and_uses_time_index(
    store: ModelOsStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(store, 1000)
    original = journal._read_summary_stream
    decoded_counts: list[int] = []

    def counted(*args: object, **kwargs: object):
        rows = original(*args, **kwargs)
        decoded_counts.append(len(rows))
        return rows

    monkeypatch.setattr(journal, "_read_summary_stream", counted)
    page = store.read_journal_page(limit=7)
    assert len(page.items) == 7
    assert len(decoded_counts) == 2
    assert all(count <= 8 for count in decoded_counts)

    assert store._conn is not None
    event_plan = store._conn.execute(
        "EXPLAIN QUERY PLAN SELECT seq FROM events WHERE seq <= ? "
        "ORDER BY occurred_at, seq LIMIT ?",
        (1000, 8),
    ).fetchall()
    decision_plan = store._conn.execute(
        "EXPLAIN QUERY PLAN SELECT seq FROM decisions WHERE seq <= ? "
        "ORDER BY decided_at, seq LIMIT ?",
        (1000, 8),
    ).fetchall()
    assert "idx_events_journal_page" in " ".join(row["detail"] for row in event_plan)
    assert "idx_decisions_journal_page" in " ".join(
        row["detail"] for row in decision_plan
    )
