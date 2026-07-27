from __future__ import annotations

from trowel_py.codex_host.child_threads import ChildThreadRegistry
from trowel_py.codex_host.session import CodexSession
from tests.codex_host.manager.support import _cfg


def test_child_thread_cannot_be_reassigned_to_another_session() -> None:
    registry = ChildThreadRegistry()
    owner = CodexSession(_cfg("owner"))
    other = CodexSession(_cfg("other"))

    assert registry.register(
        thread_id="child-thread-1",
        parent_thread_id="parent-thread-1",
        session=owner,
    )
    assert not registry.register(
        thread_id="child-thread-1",
        parent_thread_id="parent-thread-2",
        session=other,
    )
    assert registry.session_for_thread("child-thread-1") is owner


def test_child_thread_registry_rejects_self_parent_and_cycles() -> None:
    registry = ChildThreadRegistry()
    owner = CodexSession(_cfg("owner"))

    assert not registry.register(
        thread_id="child-thread-1",
        parent_thread_id="child-thread-1",
        session=owner,
    )
    assert registry.register(
        thread_id="child-thread-1",
        parent_thread_id="parent-thread-1",
        session=owner,
    )
    assert not registry.register(
        thread_id="parent-thread-1",
        parent_thread_id="child-thread-1",
        session=owner,
    )
