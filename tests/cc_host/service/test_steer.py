from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.cc_host.service._support import FakeProc, FakeSpawner, init_event, line
from trowel_py.cc_host.service import CCHost, CcSteerError


async def _start_active_turn(host: CCHost) -> tuple[object, object]:
    stream = host.send("first")
    turn_start = await anext(stream)
    session_started = await anext(stream)
    return stream, (turn_start, session_started)


@pytest.mark.anyio
async def test_steer_writes_kernel_message_into_matching_active_turn(
    tmp_path: Path,
) -> None:
    proc = FakeProc([line(init_event())], feed_eof=False)
    host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
    stream, events = await _start_active_turn(host)
    turn_start, _ = events
    generation = host.process_generation

    await host.steer(
        "kernel-soft-yield",
        expected_turn_id=turn_start.turn_id,
        expected_generation=generation,
    )

    payload = json.loads(proc.stdin.written[-1])
    assert payload == {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": "kernel-soft-yield"}],
        },
    }
    await stream.aclose()
    await host.close()


@pytest.mark.anyio
async def test_steer_rejects_stale_turn_or_process_generation_without_writing(
    tmp_path: Path,
) -> None:
    proc = FakeProc([line(init_event())], feed_eof=False)
    host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
    stream, events = await _start_active_turn(host)
    turn_start, _ = events
    written_before = list(proc.stdin.written)

    with pytest.raises(CcSteerError, match="stale turn"):
        await host.steer(
            "ignored",
            expected_turn_id=f"stale-{turn_start.turn_id}",
            expected_generation=host.process_generation,
        )
    with pytest.raises(CcSteerError, match="stale process generation"):
        await host.steer(
            "ignored",
            expected_turn_id=turn_start.turn_id,
            expected_generation="cc-process-stale",
        )

    assert proc.stdin.written == written_before
    await stream.aclose()
    await host.close()


@pytest.mark.anyio
async def test_steer_rejects_idle_session_without_starting_process(
    tmp_path: Path,
) -> None:
    proc = FakeProc([], feed_eof=False)
    spawner = FakeSpawner([proc])
    host = CCHost("sid", tmp_path, spawner=spawner)

    with pytest.raises(CcSteerError, match="no active turn"):
        await host.steer(
            "ignored",
            expected_turn_id="turn-1",
            expected_generation="cc-process-1",
        )

    assert spawner.spawned == []
    assert proc.stdin.written == []
    await host.close()
