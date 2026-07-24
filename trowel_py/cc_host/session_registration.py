"""CC 会话注册与 completed watermark 的阻塞持久化。"""

from __future__ import annotations

import os
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trowel_py.memory.sessions_repo import SessionRegistrar


def register_session(
    *,
    cc_session_id: str,
    trowel_session_id: str,
    workdir: str | os.PathLike[str],
    jsonl_path: str,
    session_kind: str,
    registrar: SessionRegistrar | None,
) -> None:
    from trowel_py.memory.sessions_repo import SessionRecord

    now = datetime.now()
    record = SessionRecord(
        cc_session_id=cc_session_id,
        workdir=str(workdir),
        date=now.date().isoformat(),
        jsonl_path=jsonl_path,
        registered_at=now.isoformat(),
        session_kind=session_kind,
        trowel_session_id=trowel_session_id,
    )
    if registrar is not None:
        registrar.register(record)
        return

    from trowel_py.memory.paths import resolve_memory_root
    from trowel_py.memory.sessions_repo import (
        create_sessions_repository,
        open_sessions_db,
    )

    connection = open_sessions_db(resolve_memory_root())
    try:
        create_sessions_repository(connection).register(record)
    finally:
        connection.close()


def update_completed(
    *,
    cc_session_id: str,
    jsonl_path: str,
    registrar: SessionRegistrar | None,
) -> None:
    try:
        completed_bytes = os.path.getsize(jsonl_path)
    except OSError:
        completed_bytes = 0

    if registrar is not None:
        registrar.update_completed(cc_session_id, completed_bytes)
        return

    from trowel_py.memory.paths import resolve_memory_root
    from trowel_py.memory.sessions_repo import (
        create_sessions_repository,
        open_sessions_db,
    )

    connection = open_sessions_db(resolve_memory_root())
    try:
        create_sessions_repository(connection).update_completed(
            cc_session_id,
            completed_bytes,
        )
    finally:
        connection.close()
