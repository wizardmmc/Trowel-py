"""画像重校准测试的共享搭建。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)

FINISHED = SimpleNamespace(type="finished")

VALID_DRAFT = json.dumps(
    {
        "suggestions": [
            {
                "dimension": "ability",
                "body": "熟悉 Python / 能编写自动化测试",
                "sources": ["示例会话提到自动化测试"],
                "rationale": "示例能力陈述",
            }
        ]
    }
)


class FakeHost:
    def __init__(self, events: list) -> None:
        self._events = events

    async def send(self, prompt: str):
        for event in self._events:
            yield event

    async def close(self) -> None:
        pass


def host_factory(draft_text: str | None, events: list | None = None):
    def factory(session: SessionRecord, workdir: Path) -> FakeHost:
        if draft_text is not None:
            (workdir / "suggestions-draft.json").write_text(
                draft_text, encoding="utf-8"
            )
        return FakeHost(events or [FINISHED])

    return factory


def seed_session(
    root: Path,
    sid: str,
    *,
    completed: int = 1000,
    jsonl_path: str = "",
    kind: str = "user",
    registered_at: str = "2026-07-14T10:00:00",
    date: str = "2026-07-14",
) -> None:
    conn = open_sessions_db(root)
    try:
        repo = create_sessions_repository(conn)
        repo.register(
            SessionRecord(
                cc_session_id=sid,
                workdir="/proj",
                date=date,
                jsonl_path=jsonl_path,
                registered_at=registered_at,
            )
        )
        repo.update_completed(sid, completed)
        if kind != "user":
            conn.execute(
                "UPDATE sessions SET session_kind=? WHERE cc_session_id=?",
                (kind, sid),
            )
            conn.commit()
    finally:
        conn.close()


def seed_live_files(root: Path) -> None:
    (root / "profile.md").write_text(
        "---\nupdated: 2026-07-01\nsource: user-edit\n---\n## 能力水平\n旧内容\n",
        encoding="utf-8",
    )
    (root / "meta").mkdir(parents=True, exist_ok=True)
    (root / "meta" / "profile-suggestions.json").write_text(
        json.dumps({"suggestions": [], "updated": "2026-07-01"}),
        encoding="utf-8",
    )
    (root / "meta" / "profile-distill-state.json").write_text(
        json.dumps({"processed": []}),
        encoding="utf-8",
    )


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def live_hashes(root: Path, jsonl_paths: list[Path]) -> dict[str, str]:
    """记录重放不得修改的全部 live 文件。"""
    hashes = {
        "profile.md": sha(root / "profile.md"),
        "suggestions": sha(root / "meta" / "profile-suggestions.json"),
        "watermark": sha(root / "meta" / "profile-distill-state.json"),
        "sessions.db": sha(root / "meta" / "sessions.db"),
    }
    for path in jsonl_paths:
        hashes[f"jsonl:{path.name}"] = sha(path)
    return hashes
