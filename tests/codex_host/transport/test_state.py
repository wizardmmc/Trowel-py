from __future__ import annotations

import asyncio
import pickle
from typing import Any

import pytest

from trowel_py.codex_host import transport
from trowel_py.codex_host.transport import AppServerClient, _TransportState
from trowel_py.codex_host.transport_state import (
    _TransportState as ExtractedTransportState,
)


def test_transport_state_preserves_legacy_identity_and_constructor_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _TransportState is ExtractedTransportState
    assert _TransportState.__module__ == "trowel_py.codex_host.transport"
    assert _TransportState.__qualname__ == "_TransportState"
    restored = pickle.loads(pickle.dumps(_TransportState()))
    assert type(restored) is _TransportState
    assert restored.pending == {}

    class SentinelState(_TransportState):
        pass

    monkeypatch.setattr(transport, "_TransportState", SentinelState)

    client = AppServerClient()

    assert type(client._state) is SentinelState
    assert client._state.pending == {}


def test_transport_state_closing_transition_is_idempotent() -> None:
    state = _TransportState()

    assert state.closed is False
    assert state.begin_closing() is True
    assert state.closed is True
    assert state.begin_closing() is False


@pytest.mark.asyncio
async def test_fail_all_detaches_pending_before_completing_futures() -> None:
    state = _TransportState()

    class InspectingFuture(asyncio.Future[dict[str, Any]]):
        def set_exception(self, exception: type[BaseException] | BaseException) -> None:
            assert state.pending == {}
            super().set_exception(exception)

    first_pending = InspectingFuture()
    second_pending = InspectingFuture()
    already_done = asyncio.get_running_loop().create_future()
    already_done.set_result({"kept": True})
    state.register("first", first_pending)
    state.register("second", second_pending)
    state.register("done", already_done)
    error = RuntimeError("transport failed")

    state.fail_all(error)

    assert state.failed is True
    assert state.closed is True
    assert first_pending.exception() is error
    assert second_pending.exception() is error
    assert already_done.result() == {"kept": True}


@pytest.mark.asyncio
async def test_pending_registry_register_pop_and_discard_keep_mapping_seam() -> None:
    state = _TransportState()
    first = asyncio.get_running_loop().create_future()
    second = asyncio.get_running_loop().create_future()

    state.register("first", first)
    state.register("second", second)

    assert state.pending == {"first": first, "second": second}
    assert state.pop("first") is first
    state.discard("second")
    assert state.pending == {}
