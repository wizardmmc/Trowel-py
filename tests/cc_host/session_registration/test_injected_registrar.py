from pathlib import Path

import pytest

from tests.cc_host.session_registration.support import (
    CapturingRegistrar,
    FailingUpdateRegistrar,
    FakeProc,
    FakeSpawner,
    init_event,
    line,
    result_event,
)
from trowel_py.cc_host.service import CCHost
from trowel_py.schemas.cc_host import FinishedEvent


async def test_cchost_uses_injected_registrar(tmp_path: Path) -> None:
    registrar = CapturingRegistrar()
    jsonl = tmp_path / "session.jsonl"
    process = FakeProc([line(init_event("cc-injected")), line(result_event())])
    host = CCHost(
        "trowel-session",
        tmp_path,
        spawner=FakeSpawner([process]),
        session_registrar=registrar,
        session_kind="review",
    )
    host._jsonl_path = lambda session_id: jsonl  # type: ignore[assignment]

    async for _ in host.send("hi"):
        pass

    assert len(registrar.registered) == 1
    record = registrar.registered[0]
    assert record.cc_session_id == "cc-injected"
    assert record.session_kind == "review"
    assert record.workdir == str(tmp_path)
    assert record.jsonl_path == str(jsonl)
    assert record.date == record.registered_at[:10]
    assert record.trowel_session_id == "trowel-session"


async def test_normal_end_updates_completed(tmp_path: Path) -> None:
    registrar = CapturingRegistrar()
    jsonl = tmp_path / "session.jsonl"
    jsonl.write_bytes(b"x" * 100)
    process = FakeProc([line(init_event("cc-watermark")), line(result_event())])
    host = CCHost(
        "trowel-session",
        tmp_path,
        spawner=FakeSpawner([process]),
        session_registrar=registrar,
    )
    host._jsonl_path = lambda session_id: jsonl  # type: ignore[assignment]

    async for _ in host.send("hi"):
        pass

    assert registrar.completed == [("cc-watermark", 100)]


# memory/profile 开关只控制读取注入，注册和水位写入必须继续。
async def test_both_switches_off_still_registers_and_stamps_completed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "trowel_py.memory.injection.resolve_memory_root",
        lambda config_path=None: tmp_path,
    )
    registrar = CapturingRegistrar()
    jsonl = tmp_path / "session.jsonl"
    jsonl.write_bytes(b"x" * 100)
    process = FakeProc([line(init_event("cc-switches-off")), line(result_event())])
    host = CCHost(
        "trowel-session",
        tmp_path,
        spawner=FakeSpawner([process]),
        session_registrar=registrar,
        memory_enabled=False,
        profile_enabled=False,
    )
    host._jsonl_path = lambda session_id: jsonl  # type: ignore[assignment]

    async for _ in host.send("hi"):
        pass

    assert len(registrar.registered) == 1
    assert registrar.registered[0].cc_session_id == "cc-switches-off"
    assert registrar.completed == [("cc-switches-off", 100)]
    assert host.memory_enabled is False
    assert host.profile_enabled is False
    assert host._mcp_config is None  # noqa: SLF001


def test_missing_jsonl_stamps_zero(tmp_path: Path) -> None:
    registrar = CapturingRegistrar()
    host = CCHost(
        "trowel-session",
        tmp_path,
        spawner=FakeSpawner([]),
        session_registrar=registrar,
    )
    host._cc_session_id = "cc-missing-jsonl"

    host._update_completed_blocking(str(tmp_path / "missing.jsonl"))

    assert registrar.completed == [("cc-missing-jsonl", 0)]


def test_missing_native_session_id_skips_registration_and_update(
    tmp_path: Path,
) -> None:
    registrar = CapturingRegistrar()
    host = CCHost(
        "trowel-session",
        tmp_path,
        spawner=FakeSpawner([]),
        session_registrar=registrar,
    )

    host._register_session_blocking(str(tmp_path / "session.jsonl"))
    host._update_completed_blocking(str(tmp_path / "session.jsonl"))

    assert registrar.registered == []
    assert registrar.completed == []


async def test_update_failure_does_not_break_result_turn(
    tmp_path: Path,
) -> None:
    registrar = FailingUpdateRegistrar()
    process = FakeProc([line(init_event("cc-update-failure")), line(result_event())])
    host = CCHost(
        "trowel-session",
        tmp_path,
        spawner=FakeSpawner([process]),
        session_registrar=registrar,
    )
    host._jsonl_path = lambda session_id: tmp_path / "missing.jsonl"  # type: ignore[assignment]

    events = [event async for event in host.send("hi")]

    assert len(registrar.registered) == 1
    assert any(isinstance(event, FinishedEvent) for event in events)
