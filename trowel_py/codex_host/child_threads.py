"""记录 Codex 子线程及其所属的 Trowel 会话。"""

from __future__ import annotations

from dataclasses import dataclass

from trowel_py.codex_host.session import CodexSession


@dataclass(frozen=True)
class ChildThreadBinding:
    """保存一个 Codex 子线程的父线程和所属会话。

    Attributes:
        thread_id: Codex 子线程 ID。
        parent_thread_id: Codex 直接父线程 ID。
        session: 根线程所属的 Trowel 会话，子线程事件也发送到该会话。
    """

    thread_id: str
    parent_thread_id: str
    session: CodexSession


class ChildThreadRegistry:
    """维护 Codex 子线程到根线程所属 Trowel 会话的归属关系。"""

    def __init__(self) -> None:
        """创建一个空的子线程归属表。"""

        self._bindings: dict[str, ChildThreadBinding] = {}

    def register(
        self,
        *,
        thread_id: str,
        parent_thread_id: str,
        session: CodexSession,
    ) -> bool:
        """登记子线程，并拒绝自指、成环或冲突的归属关系。

        重复登记完全相同的关系视为成功。

        Args:
            thread_id: 要登记的 Codex 子线程 ID。
            parent_thread_id: 该子线程的直接父线程 ID。
            session: 根线程所属的 Trowel 会话。

        Returns:
            关系已登记或原本一致时返回 True；关系无效或与已有登记冲突时返回
            False。
        """

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
        """返回子线程所属的 Trowel 会话；未登记时返回 None。"""

        binding = self._bindings.get(thread_id)
        return binding.session if binding is not None else None

    def is_child(self, thread_id: str) -> bool:
        """返回线程是否登记为子线程。"""

        return thread_id in self._bindings

    def remove_session(self, session: CodexSession) -> tuple[str, ...]:
        """移除指定会话的全部子线程，并返回被移除的线程 ID。"""

        removed = tuple(
            thread_id
            for thread_id, binding in self._bindings.items()
            if binding.session is session
        )
        for thread_id in removed:
            self._bindings.pop(thread_id, None)
        return removed
