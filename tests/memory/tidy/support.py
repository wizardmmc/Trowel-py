"""Tidy 计划测试的共享搭建。"""

from __future__ import annotations

from pathlib import Path

from trowel_py.memory.store import MemoryStore
from trowel_py.memory.tidy import TidyOperation, TidyPlan


class FakeProvider:
    """依次返回预设响应，并记录每次调用。"""

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self.responses.pop(0) if self.responses else "{}"


def write_note(
    root: Path,
    memory_id: str,
    title: str,
    *,
    sources: tuple[str, ...] = (),
    content_hash: str = "h1",
) -> str:
    return MemoryStore(root).write_note(
        {
            "type": "note",
            "title": title,
            "verification": "verified",
            "memory_id": memory_id,
            "status": "active",
            "sources": list(sources),
            "content_hash": content_hash,
            "__body": f"body of {title}",
        }
    )


def snapshot(root: Path) -> dict[str, str]:
    return {
        note.memory_id: note.content_hash
        for _stem, note in MemoryStore(root).load_notes_with_id()
        if note.memory_id
    }


def make_plan(
    operations: tuple[TidyOperation, ...],
    root: Path,
    plan_id: str = "p1",
) -> TidyPlan:
    return TidyPlan(
        plan_id=plan_id,
        source_snapshot=snapshot(root),
        operations=operations,
    )
