"""Warm pool capacity, ordering and concurrency (slice-086).

Covers spec pass criteria 2, 3 (warm side), 4 (warm order), 8:
- warm_limit default 3; 4th promote raises WarmFull with current warm ids (2)
- foreground and waiting Tasks count against warm_limit (3)
- warm order: created_at by default, warm_rank overrides; user can demote (4)
- concurrent promote never exceeds warm_limit (8)
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from trowel_py.model_os.store import ModelOsStore, WarmFull
from trowel_py.model_os.types import TaskStatus


# ----------------------------------------------------------- capacity (2,3) ---


def test_warm_limit_default_is_three(store: ModelOsStore) -> None:
    """Default warm_limit=3 (slice-086 grill decision 7)."""

    assert store._warm_limit == 3


def test_fourth_promote_raises_warm_full_with_current_warm_ids(
    store: ModelOsStore,
) -> None:
    """The 4th Task cannot enter warm; WarmFull carries the current warm ids
    so the caller/UI can surface the replacement choice (pass criterion 2)."""

    t1 = store.create_task_from_user_request(
        original_goal="1", idempotency_key="k1", authorization_scope="d"
    )
    t2 = store.create_task_from_user_request(
        original_goal="2", idempotency_key="k2", authorization_scope="d"
    )
    t3 = store.create_task_from_user_request(
        original_goal="3", idempotency_key="k3", authorization_scope="d"
    )
    t4 = store.create_task_from_user_request(
        original_goal="4", idempotency_key="k4", authorization_scope="d"
    )
    for t in (t1, t2, t3):
        store.promote_to_warm(t.task_id)

    with pytest.raises(WarmFull) as exc:
        store.promote_to_warm(t4.task_id)
    assert exc.value.limit == 3
    assert set(exc.value.warm_task_ids) == {t1.task_id, t2.task_id, t3.task_id}
    # t4 stayed backlog
    snap = store.read_snapshot()
    t4_state = next(ts for ts in snap.tasks if ts.task_id == t4.task_id)
    assert t4_state.status == TaskStatus.BACKLOG
    assert t4_state.warm is False


def test_foreground_counts_against_warm_limit(store: ModelOsStore) -> None:
    """foreground ⇒ warm; the running Task occupies one warm slot (pass 3)."""

    t1 = store.create_task_from_user_request(
        original_goal="1", idempotency_key="k1", authorization_scope="d"
    )
    store.promote_to_warm(t1.task_id)
    store.claim_foreground(t1.task_id)
    t2 = store.create_task_from_user_request(
        original_goal="2", idempotency_key="k2", authorization_scope="d"
    )
    t3 = store.create_task_from_user_request(
        original_goal="3", idempotency_key="k3", authorization_scope="d"
    )
    store.promote_to_warm(t2.task_id)
    store.promote_to_warm(t3.task_id)
    # now warm is full (t1 running + t2 + t3); 4th promote fails
    t4 = store.create_task_from_user_request(
        original_goal="4", idempotency_key="k4", authorization_scope="d"
    )
    with pytest.raises(WarmFull):
        store.promote_to_warm(t4.task_id)


def test_waiting_task_keeps_warm_slot(store: ModelOsStore) -> None:
    """waiting_user defaults to keeping warm (grill decision 8); so a waiting
    task still counts against warm_limit."""

    tasks = [
        store.create_task_from_user_request(
            original_goal=str(i), idempotency_key=f"k{i}", authorization_scope="d"
        )
        for i in range(3)
    ]
    for t in tasks:
        store.promote_to_warm(t.task_id)
    store.claim_foreground(tasks[0].task_id)
    store.set_waiting_user(
        tasks[0].task_id, cause="等回复", correlation_id="q1"
    )
    # tasks[0] is now waiting_user but still warm; warm is full
    t4 = store.create_task_from_user_request(
        original_goal="4", idempotency_key="k4", authorization_scope="d"
    )
    with pytest.raises(WarmFull):
        store.promote_to_warm(t4.task_id)


# ------------------------------------------------------- order / demote (4) ---


def test_warm_order_defaults_to_created_at(store: ModelOsStore) -> None:
    """Without explicit warm_rank, warm tasks order by created_at (pass 6)."""

    t1 = store.create_task_from_user_request(
        original_goal="1", idempotency_key="k1", authorization_scope="d"
    )
    t2 = store.create_task_from_user_request(
        original_goal="2", idempotency_key="k2", authorization_scope="d"
    )
    t3 = store.create_task_from_user_request(
        original_goal="3", idempotency_key="k3", authorization_scope="d"
    )
    for t in (t1, t2, t3):
        store.promote_to_warm(t.task_id)
    snap = store.read_snapshot()
    assert [t.task_id for t in snap.warm_tasks()] == [
        t1.task_id,
        t2.task_id,
        t3.task_id,
    ]


def test_warm_rank_overrides_created_order(store: ModelOsStore) -> None:
    """User-set warm_rank takes precedence over created_at (pass 6)."""

    t1 = store.create_task_from_user_request(
        original_goal="1", idempotency_key="k1", authorization_scope="d"
    )
    t2 = store.create_task_from_user_request(
        original_goal="2", idempotency_key="k2", authorization_scope="d"
    )
    t3 = store.create_task_from_user_request(
        original_goal="3", idempotency_key="k3", authorization_scope="d"
    )
    for t in (t1, t2, t3):
        store.promote_to_warm(t.task_id)
    # make t3 first, t1 second, t2 third
    store.set_warm_rank(t3.task_id, 1)
    store.set_warm_rank(t1.task_id, 2)
    store.set_warm_rank(t2.task_id, 3)
    snap = store.read_snapshot()
    assert [t.task_id for t in snap.warm_tasks()] == [
        t3.task_id,
        t1.task_id,
        t2.task_id,
    ]


def test_demote_to_backlog_frees_warm_slot(store: ModelOsStore) -> None:
    """Demoting a warm Task to backlog frees its slot; a new Task can promote."""

    t1 = store.create_task_from_user_request(
        original_goal="1", idempotency_key="k1", authorization_scope="d"
    )
    t2 = store.create_task_from_user_request(
        original_goal="2", idempotency_key="k2", authorization_scope="d"
    )
    t3 = store.create_task_from_user_request(
        original_goal="3", idempotency_key="k3", authorization_scope="d"
    )
    for t in (t1, t2, t3):
        store.promote_to_warm(t.task_id)
    store.demote_to_backlog(t2.task_id)

    t4 = store.create_task_from_user_request(
        original_goal="4", idempotency_key="k4", authorization_scope="d"
    )
    store.promote_to_warm(t4.task_id)  # now ok — slot freed

    snap = store.read_snapshot()
    warm_ids = {t.task_id for t in snap.warm_tasks()}
    assert warm_ids == {t1.task_id, t3.task_id, t4.task_id}
    t2_state = next(ts for ts in snap.tasks if ts.task_id == t2.task_id)
    assert t2_state.status == TaskStatus.BACKLOG
    assert t2_state.warm is False


def test_demote_foreground_refused(store: ModelOsStore) -> None:
    """A foreground Task cannot be demoted; release foreground first."""

    t1 = store.create_task_from_user_request(
        original_goal="1", idempotency_key="k1", authorization_scope="d"
    )
    store.promote_to_warm(t1.task_id)
    store.claim_foreground(t1.task_id)
    with pytest.raises(Exception):
        store.demote_to_backlog(t1.task_id)


# ---------------------------------------------------- concurrency (8) ---


def test_concurrent_promote_never_exceeds_limit(db_path: Path) -> None:
    """Two connections each promote Tasks into a warm_limit=2 pool: the
    derived warm count never exceeds the limit (pass criterion 8). IMMEDIATE
    transactions serialise the count-check-then-write window."""

    store_a = ModelOsStore(db_path, warm_limit=2)
    store_a.open()
    store_b = ModelOsStore(db_path, warm_limit=2)
    store_b.open()
    try:
        ids = []
        for i in range(4):
            ids.append(
                store_a.create_task_from_user_request(
                    original_goal=str(i),
                    idempotency_key=f"k{i}",
                    authorization_scope="d",
                ).task_id
            )

        results: list[str] = []

        def promote(s: ModelOsStore, tid: str, label: str) -> None:
            try:
                s.promote_to_warm(tid)
                results.append(f"{label}:ok")
            except WarmFull:
                results.append(f"{label}:full")

        with ThreadPoolExecutor(max_workers=4) as pool:
            pool.submit(promote, store_a, ids[0], "a0")
            pool.submit(promote, store_a, ids[1], "a1")
            pool.submit(promote, store_b, ids[2], "b2")
            pool.submit(promote, store_b, ids[3], "b3")

        snap = store_a.read_snapshot()
        assert len(snap.warm_tasks()) <= 2
        # exactly 2 wins, 2 full (limit=2, 4 contenders)
        assert results.count("a0:ok") + results.count("a1:ok") + \
            results.count("b2:ok") + results.count("b3:ok") == 2
    finally:
        store_a.close()
        store_b.close()
