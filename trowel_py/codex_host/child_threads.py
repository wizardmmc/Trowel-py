"""Track native Codex child-thread ownership within one host process."""

from __future__ import annotations

from dataclasses import dataclass

from trowel_py.codex_host.session import CodexSession


@dataclass(frozen=True)
class ChildThreadBinding:
    thread_id: str
    parent_thread_id: str
    session: CodexSession


class ChildThreadRegistry:
    """Map child threads to the Trowel session that owns their root thread."""

    def __init__(self) -> None:
        self._bindings: dict[str, ChildThreadBinding] = {}

    def register(
        self,
        *,
        thread_id: str,
        parent_thread_id: str,
        session: CodexSession,
    ) -> bool:
        if thread_id == parent_thread_id:
            return False
        existing = self._bindings.get(thread_id)
        if existing is not None:
            return (
                existing.session is session
                and existing.parent_thread_id == parent_thread_id
            )
        ancestor_id = parent_thread_id
        visited: set[str] = set()
        while ancestor_id not in visited:
            if ancestor_id == thread_id:
                return False
            visited.add(ancestor_id)
            ancestor = self._bindings.get(ancestor_id)
            if ancestor is None:
                break
            ancestor_id = ancestor.parent_thread_id
        self._bindings[thread_id] = ChildThreadBinding(
            thread_id=thread_id,
            parent_thread_id=parent_thread_id,
            session=session,
        )
        return True

    def session_for_thread(self, thread_id: str) -> CodexSession | None:
        binding = self._bindings.get(thread_id)
        return binding.session if binding is not None else None

    def is_child(self, thread_id: str) -> bool:
        return thread_id in self._bindings

    def remove_session(self, session: CodexSession) -> tuple[str, ...]:
        removed = tuple(
            thread_id
            for thread_id, binding in self._bindings.items()
            if binding.session is session
        )
        for thread_id in removed:
            self._bindings.pop(thread_id, None)
        return removed
