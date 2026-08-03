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
    """将 CC 会话、Trowel 会话 ID 和 transcript 路径登记到 Memory 会话库。

    未注入 registrar 时打开默认 Memory 数据库，登记后始终关闭连接。

    Args:
        cc_session_id: CC 返回的原生会话 ID。
        trowel_session_id: Trowel 分配的会话 ID。
        workdir: CC 会话使用的工作目录。
        jsonl_path: CC 保存该会话 transcript 的 JSONL 路径。
        session_kind: 会话来源，例如 `user` 或 `delegate`。
        registrar: 接收会话记录的注册器；`None` 表示使用默认 Memory 数据库。
    """

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
        create_sessions_repository(connection).claude.register(record)
    finally:
        connection.close()


def update_completed(
    *,
    cc_session_id: str,
    trowel_session_id: str,
    jsonl_path: str,
    status: str,
    registrar: SessionRegistrar | None,
) -> None:
    """保存 transcript 完成水位和当前 Trowel binding 的终态。

    transcript 不存在或无法读取大小时保存 0。未注入 registrar 时使用默认
    Memory 数据库，并在更新后始终关闭连接。

    Args:
        cc_session_id: 要更新的原生 CC 会话 ID。
        trowel_session_id: 要更新终态的 Trowel 会话 ID。
        jsonl_path: 已完整处理到轮次边界的 transcript 文件路径。
        status: completed、interrupted 或 failed。
        registrar: 接收水位更新的注册器；`None` 表示使用默认 Memory 数据库。
    """

    try:
        completed_bytes = os.path.getsize(jsonl_path)
    except OSError:
        completed_bytes = 0

    if registrar is not None:
        registrar.update_completed(cc_session_id, completed_bytes)
        status_updater = getattr(registrar, "update_binding_status", None)
        if callable(status_updater):
            status_updater(
                trowel_session_id,
                status=status,
                completed_at=datetime.now().astimezone().isoformat(),
            )
        return

    from trowel_py.memory.paths import resolve_memory_root
    from trowel_py.memory.sessions_repo import (
        create_sessions_repository,
        open_sessions_db,
    )

    connection = open_sessions_db(resolve_memory_root())
    try:
        repository = create_sessions_repository(connection).claude
        repository.update_completed(
            cc_session_id,
            completed_bytes,
        )
        repository.update_binding_status(
            trowel_session_id,
            status=status,
            completed_at=datetime.now().astimezone().isoformat(),
        )
    finally:
        connection.close()
