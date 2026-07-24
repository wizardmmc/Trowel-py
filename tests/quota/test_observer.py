from __future__ import annotations

import json
from pathlib import Path

import pytest

from trowel_py.quota.codex import make_codex_observer
from trowel_py.quota.read_model import QuotaReadModel
from trowel_py.quota.types import Provider, QuotaStatus, QuotaWindowKind

FIXTURES = Path(__file__).parent / "fixtures"
_PAYLOAD_PATH = FIXTURES / "codex_ratelimit_real.json"
pytestmark = pytest.mark.skipif(
    not _PAYLOAD_PATH.exists(),
    reason="local Codex recording is gitignored; not present on a fresh clone",
)


def _payload() -> dict:
    return json.loads(_PAYLOAD_PATH.read_text(encoding="utf-8"))


def test_observer_maps_rate_limit_envelope_into_read_model() -> None:
    rm = QuotaReadModel(now_ms=lambda: 1_000, stale_after_ms=10**9)
    observe = make_codex_observer(rm, now_ms=lambda: 1_000)

    observe({"type": "rate_limit_updated", "payload": _payload()})

    got = rm.get(Provider.CODEX, "codex")
    assert got is not None
    assert got.status is QuotaStatus.OK
    assert got.windows[0].kind is QuotaWindowKind.RATE_LIMIT
    assert got.windows[0].used_percent == 20


def test_observer_ignores_non_rate_limit_envelopes_but_records_no_data() -> None:
    rm = QuotaReadModel(now_ms=lambda: 0)
    observe = make_codex_observer(rm)

    observe({"type": "status", "payload": {}})
    observe({"type": "message"})
    observe({"type": "rate_limit_updated", "payload": {"primary": None}})

    got = rm.get(Provider.CODEX, "codex")
    assert got is not None
    assert got.status is QuotaStatus.NO_DATA


def test_observer_never_raises_on_malformed_envelopes() -> None:
    rm = QuotaReadModel(now_ms=lambda: 0)
    observe = make_codex_observer(rm)
    for bad in (
        None,
        "not-a-dict",
        {},
        {"type": "rate_limit_updated"},
        {"type": "rate_limit_updated", "payload": "not-a-dict"},
        {"type": "rate_limit_updated", "payload": {"primary": "x"}},
    ):
        observe(bad)


def test_hub_observer_exception_does_not_propagate(tmp_path) -> None:
    """SSE 路径上的 observer 异常不能中断用户 turn。"""

    from trowel_py.agent_host import BindingStore, SessionHub

    def raising(_payload: object) -> None:
        raise RuntimeError("observer boom")

    hub = SessionHub(BindingStore(tmp_path / "bindings.json"), event_observer=raising)
    hub._observe({"type": "rate_limit_updated", "payload": {}})


def test_observer_marks_codex_stale_on_turn_terminal() -> None:
    rm = QuotaReadModel(now_ms=lambda: 0, stale_after_ms=10**9)
    observe = make_codex_observer(rm, now_ms=lambda: 1000)

    observe({"type": "rate_limit_updated", "payload": _payload()})
    assert rm.get(Provider.CODEX, "codex").status is QuotaStatus.OK

    observe({"type": "finished", "payload": {}})
    assert rm.get(Provider.CODEX, "codex").status is QuotaStatus.STALE
