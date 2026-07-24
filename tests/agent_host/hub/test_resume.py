from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.agent_host.binding import Runtime, make_binding
from trowel_py.agent_host.hub import (
    ConditionMismatchError,
    CrossRuntimeResumeError,
    SessionHub,
)
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
