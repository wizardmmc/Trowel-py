"""judge 测试数据与 fake host。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from trowel_py.memory.access_log import AccessRecord
from trowel_py.memory.sessions_repo import SessionRecord

FINISHED = SimpleNamespace(type="finished")
ERROR = SimpleNamespace(type="error")

_VALID_DRAFT = json.dumps(
    {
        "hits": [
            {
                "memory_id": "real-note",
                "used": True,
                "outcome": "helpful",
                "reason": "模型引用了它",
                "evidence": "turn 3 改方向",
            }
        ],
        "recall_miss": [
            {
                "memory_id": "real-note-2",
                "attribution": "retrieval_miss",
                "reason": "当时没搜到",
                "evidence": "无相关 search",
            }
        ],
        "summary": "用得还行",
    }
)


class FakeHost:
    def __init__(self, events: list) -> None:
        self._events = events

    async def send(self, prompt: str):
        for ev in self._events:
            yield ev

    async def close(self) -> None:
        pass


def _factory(events: list, draft_text: str | None = None):
    def factory(session: SessionRecord, workdir: Path) -> FakeHost:
        if draft_text is not None:
            (workdir / "judgement-draft.json").write_text(draft_text, encoding="utf-8")
        return FakeHost(events)

    return factory


def _session(sid: str = "judged-1") -> SessionRecord:
    return SessionRecord(
        cc_session_id=sid,
        workdir="/proj",
        date="2026-07-16",
        jsonl_path="/x.jsonl",
        registered_at="2026-07-16T10:00:00",
    )


def _seed_real_notes(root: Path, memory_ids: tuple[str, ...]) -> None:
    """直接写 Markdown，以便显式构造 `write_note` 不写入的 memory_id。"""
    ndir = root / "notes"
    ndir.mkdir(parents=True, exist_ok=True)
    for mid in memory_ids:
        (ndir / f"{mid}.md").write_text(
            "---\n"
            "type: note\n"
            f"title: {mid}\n"
            "kind: fact\n"
            f"summary: {mid}\n"
            "verification: verified\n"
            f"memory_id: {mid}\n"
            "---\n"
            "body\n",
            encoding="utf-8",
        )


def _access(
    cc_session_id: str, action: str, memory_id: str = "", query: str = ""
) -> AccessRecord:
    return AccessRecord(
        ts="2026-07-16T10:00:00",
        trowel_session_id="t1",
        cc_session_id=cc_session_id,
        toolUseId="tu-1",
        action=action,  # type: ignore[arg-type]
        search_id="s1" if action == "search" else "",
        read_id="r1" if action == "read" else "",
        query=query,
        memory_id=memory_id,
        rank=1 if action == "search" else None,
    )
