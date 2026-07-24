from __future__ import annotations

from trowel_py.quota.read_model import QuotaReadModel
from trowel_py.quota.types import (
    Provider,
    QuotaSnapshot,
    QuotaStatus,
    QuotaWindow,
    QuotaWindowKind,
)


def _ok(
    provider: Provider, account: str, fetched_at: int, used: float = 50
) -> QuotaSnapshot:
    return QuotaSnapshot(
        provider=provider,
        account_id=account,
        plan_level="max",
        windows=(
            QuotaWindow(
                kind=QuotaWindowKind.WEEKLY,
                used_percent=used,
                resets_at=None,
                raw={},
            ),
        ),
        fetched_at=fetched_at,
        status=QuotaStatus.OK,
    )


def _err(
    provider: Provider, account: str, fetched_at: int, status: QuotaStatus
) -> QuotaSnapshot:
    return QuotaSnapshot(
        provider=provider,
        account_id=account,
        plan_level=None,
        windows=(),
        fetched_at=fetched_at,
        status=status,
    )


def test_update_then_get_returns_fresh_snapshot() -> None:
    clock = [1_000]
    rm = QuotaReadModel(now_ms=lambda: clock[0], stale_after_ms=600_000)
    rm.update(_ok(Provider.GLM, "glm-a", fetched_at=1_000, used=50))

    got = rm.get(Provider.GLM, "glm-a")
    assert got is not None
    assert got.status is QuotaStatus.OK
    assert got.windows[0].used_percent == 50


def test_unknown_account_returns_none() -> None:
    rm = QuotaReadModel(now_ms=lambda: 0)
    assert rm.get(Provider.GLM, "never") is None


def test_ok_snapshot_goes_stale_after_threshold_and_keeps_windows() -> None:
    clock = [1_000]
    rm = QuotaReadModel(now_ms=lambda: clock[0], stale_after_ms=600_000)
    rm.update(_ok(Provider.GLM, "a", fetched_at=1_000))

    clock[0] = 1_000 + 600_001
    got = rm.get(Provider.GLM, "a")
    assert got is not None
    assert got.status is QuotaStatus.STALE
    assert len(got.windows) == 1


def test_error_status_is_not_relabeled_stale() -> None:
    rm = QuotaReadModel(now_ms=lambda: 10**12, stale_after_ms=1)
    rm.update(_err(Provider.GLM, "a", fetched_at=0, status=QuotaStatus.AUTH_ERROR))

    got = rm.get(Provider.GLM, "a")
    assert got is not None
    assert got.status is QuotaStatus.AUTH_ERROR


def test_all_lists_each_account_and_applies_staleness() -> None:
    clock = [0]
    rm = QuotaReadModel(now_ms=lambda: clock[0], stale_after_ms=10)
    rm.update(_ok(Provider.GLM, "a", fetched_at=0))
    rm.update(_ok(Provider.CODEX, "c", fetched_at=0))

    clock[0] = 100
    by_key = {(s.provider, s.account_id): s.status for s in rm.all()}
    assert by_key[(Provider.GLM, "a")] is QuotaStatus.STALE
    assert by_key[(Provider.CODEX, "c")] is QuotaStatus.STALE


def test_update_replaces_previous_snapshot() -> None:
    rm = QuotaReadModel(now_ms=lambda: 0, stale_after_ms=10**9)
    rm.update(_ok(Provider.GLM, "a", fetched_at=0, used=10))
    rm.update(_ok(Provider.GLM, "a", fetched_at=0, used=90))

    got = rm.get(Provider.GLM, "a")
    assert got is not None
    assert got.windows[0].used_percent == 90


def test_mark_stale_flips_ok_to_stale_and_keeps_windows() -> None:
    rm = QuotaReadModel(now_ms=lambda: 0, stale_after_ms=10**9)
    rm.update(_ok(Provider.CODEX, "codex", fetched_at=0))

    rm.mark_stale(Provider.CODEX, "codex")

    got = rm.get(Provider.CODEX, "codex")
    assert got is not None
    assert got.status is QuotaStatus.STALE
    assert len(got.windows) == 1


def test_mark_stale_leaves_error_and_missing_alone() -> None:
    rm = QuotaReadModel(now_ms=lambda: 0)
    rm.update(
        _err(Provider.CODEX, "codex", fetched_at=0, status=QuotaStatus.AUTH_ERROR)
    )

    rm.mark_stale(Provider.CODEX, "codex")
    rm.mark_stale(Provider.CODEX, "missing")

    assert rm.get(Provider.CODEX, "codex").status is QuotaStatus.AUTH_ERROR
    assert rm.get(Provider.CODEX, "missing") is None


def test_all_is_safe_under_concurrent_writer() -> None:
    """其他线程持续写入时，读取快照不能因字典大小变化而失败。"""

    import threading

    rm = QuotaReadModel(now_ms=lambda: 0, stale_after_ms=10**9)
    stop = threading.Event()

    def writer() -> None:
        i = 0
        while not stop.is_set():
            rm.update(_ok(Provider.GLM, f"a{i % 50}", fetched_at=0))
            i += 1

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        for _ in range(2000):
            rm.all()
    finally:
        stop.set()
        thread.join()
