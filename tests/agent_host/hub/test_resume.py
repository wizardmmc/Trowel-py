from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.agent_host.binding import Runtime, make_binding
from trowel_py.agent_host.hub import (
    ConditionMismatchError,
    CrossRuntimeResumeError,
    SessionHub,
)
from trowel_py.cc_host.session_scan import SessionConfigSummary
from tests.agent_host.hub._support import (
    FakeCodexManager,
    cc_req,
    codex_req,
)


def test_create_cc_with_resume_carries_native_id(hub: SessionHub, workdir: Path):
    binding = hub.create(cc_req(workdir, resume_from="cc-jsonl-9"))
    assert binding.native_session_id == "cc-jsonl-9"


def test_create_codex_with_resume_seeds_thread_binding(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):

    binding = hub.create(
        codex_req(workdir, resume_from="codex-thread-abc")
    )
    session = codex_mgr.get_session(binding.session_id)
    assert session is not None
    assert session.config.initial_thread_id == "codex-thread-abc"
    assert session.binding is not None
    assert session.binding.thread_id == "codex-thread-abc"
    assert session.is_new_thread is False


def test_resume_codex_inherits_frozen_conditions_but_leaves_model_to_native_resume(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
) -> None:
    hub._store.put(
        make_binding(
            session_id="old-codex",
            runtime=Runtime.CODEX,
            native_session_id="codex-thread-configured",
            workdir=str(workdir),
            model="gpt-5.6-sol",
            effort="ultra",
            permission="Full access · never",
            permission_preset="danger-full-access",
            memory_enabled=False,
            profile_enabled=True,
            capabilities=("tools",),
            name="proj",
        )
    )

    resumed = hub.create(
        codex_req(workdir, resume_from="codex-thread-configured")
    )
    session = codex_mgr.get_session(resumed.session_id)

    assert resumed.model is None
    assert resumed.effort is None
    assert resumed.permission_preset == "danger-full-access"
    assert resumed.memory_enabled is False
    assert resumed.profile_enabled is True
    assert session.config.model is None
    assert session.config.effort is None


def test_resume_cc_inherits_model_effort_and_permission(
    hub: SessionHub, workdir: Path
) -> None:
    hub._store.put(
        make_binding(
            session_id="old-cc",
            runtime=Runtime.CLAUDE_CODE,
            native_session_id="cc-jsonl-configured",
            workdir=str(workdir),
            model="opus",
            effort="max",
            permission="acceptEdits",
            memory_enabled=True,
            profile_enabled=False,
            capabilities=("tools",),
            name="proj",
        )
    )

    resumed = hub.create(cc_req(workdir, resume_from="cc-jsonl-configured"))
    host = hub._cc_registry[resumed.session_id]

    assert resumed.model == "opus"
    assert resumed.effort == "max"
    assert resumed.permission == "acceptEdits"
    assert resumed.profile_enabled is False
    assert host.model == "opus"
    assert host.effort == "max"
    assert host.permission_mode == "acceptEdits"


async def test_prepare_external_cc_resume_reads_native_config_off_event_loop(
    hub: SessionHub,
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "trowel_py.cc_host.session_scan.read_session_config",
        lambda _workdir, _session_id: SessionConfigSummary(
            model="glm-5.2",
            effort="max",
            permission_mode="bypassPermissions",
        ),
    )

    prepared = await hub.prepare_create_request(
        cc_req(
            workdir,
            resume_from="00000000-0000-4000-8000-000000000099",
        )
    )
    resumed = hub.create(prepared)

    assert resumed.model == "glm-5.2"
    assert resumed.effort == "max"
    assert resumed.permission == "bypassPermissions"


def test_validate_resume_rejects_cross_runtime(hub: SessionHub, workdir: Path):

    hub._store.put(
        make_binding(
            session_id="old-cc",
            runtime=Runtime.CLAUDE_CODE,
            native_session_id="cc-jsonl-1",
            workdir=str(workdir),
            model=None,
            effort=None,
            permission=None,
            memory_enabled=True,
            profile_enabled=True,
            capabilities=("tools",),
            name="proj",
        )
    )
    with pytest.raises(CrossRuntimeResumeError):
        hub.validate_resume(Runtime.CODEX, "cc-jsonl-1")


def test_validate_resume_same_runtime_ok(hub: SessionHub):
    hub.validate_resume(Runtime.CLAUDE_CODE, "fresh-cc-id")
    hub.validate_resume(Runtime.CODEX, None)


def test_validate_resume_rejects_mp_mismatch_for_same_native_thread(
    hub: SessionHub, workdir: Path
) -> None:

    # 已绑定 native thread 的实验条件不可变，否则恢复会串换 memory/profile 条件。
    hub._store.put(
        make_binding(
            session_id="orig-mem-on",
            runtime=Runtime.CODEX,
            native_session_id="thr-frozen",
            workdir=str(workdir),
            model=None,
            effort=None,
            permission=None,
            memory_enabled=True,
            profile_enabled=True,
            capabilities=("tools",),
            name="proj",
        )
    )

    with pytest.raises(ConditionMismatchError):
        hub.validate_resume(
            Runtime.CODEX,
            "thr-frozen",
            memory_enabled=False,
            profile_enabled=True,
        )

    with pytest.raises(ConditionMismatchError):
        hub.validate_resume(
            Runtime.CODEX,
            "thr-frozen",
            memory_enabled=True,
            profile_enabled=False,
        )


def test_validate_resume_rejects_self_enabled_mismatch_for_same_native_thread(
    hub: SessionHub, workdir: Path
) -> None:

    hub._store.put(
        make_binding(
            session_id="orig-self-on",
            runtime=Runtime.CODEX,
            native_session_id="thr-self-frozen",
            workdir=str(workdir),
            model=None,
            effort=None,
            permission=None,
            memory_enabled=True,
            profile_enabled=True,
            self_enabled=True,
            capabilities=("tools",),
            name="proj",
        )
    )
    with pytest.raises(ConditionMismatchError):
        hub.validate_resume(
            Runtime.CODEX,
            "thr-self-frozen",
            memory_enabled=True,
            profile_enabled=True,
            self_enabled=False,
        )


def test_validate_resume_allows_matching_mp(hub: SessionHub, workdir: Path) -> None:

    hub._store.put(
        make_binding(
            session_id="orig-mem-on",
            runtime=Runtime.CODEX,
            native_session_id="thr-frozen",
            workdir=str(workdir),
            model=None,
            effort=None,
            permission=None,
            memory_enabled=True,
            profile_enabled=False,
            capabilities=("tools",),
            name="proj",
        )
    )
    hub.validate_resume(
        Runtime.CODEX,
        "thr-frozen",
        memory_enabled=True,
        profile_enabled=False,
    )


def test_validate_resume_skips_mp_check_when_caller_omits_switches(
    hub: SessionHub, workdir: Path
) -> None:

    hub._store.put(
        make_binding(
            session_id="orig",
            runtime=Runtime.CODEX,
            native_session_id="thr-old",
            workdir=str(workdir),
            model=None,
            effort=None,
            permission=None,
            memory_enabled=True,
            profile_enabled=True,
            capabilities=("tools",),
            name="proj",
        )
    )
    hub.validate_resume(Runtime.CODEX, "thr-old")
