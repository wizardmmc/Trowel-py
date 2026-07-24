"""画像重校准的只读计划。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db_readonly,
)

from .models import (
    _EXCLUDE_KINDS,
    _LIVE_PROFILE,
    _LIVE_SUGGESTIONS,
    _LIVE_WATERMARK,
    FrozenSession,
    LiveHashes,
    RecalibrationPlan,
    RecalibrationScopeError,
)


def _validate_scope(*, scope_all: bool, from_date: str | None) -> None:
    """要求且仅允许一种重放范围。"""
    if scope_all and from_date is not None:
        raise RecalibrationScopeError("specify either --all or --from, not both")
    if not scope_all and from_date is None:
        raise RecalibrationScopeError("must specify --all or --from")


def _sha256_file(path: Path) -> str | None:
    """返回文件摘要；缺失文件不伪造内容。"""
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _live_hashes(root: Path) -> LiveHashes:
    """读取三个 live 文件的摘要。"""
    return LiveHashes(
        profile=_sha256_file(root / _LIVE_PROFILE[1]),
        suggestions=_sha256_file(root / _LIVE_SUGGESTIONS[1]),
        watermark=_sha256_file(root / _LIVE_WATERMARK[1]),
    )


def _session_day(session: SessionRecord) -> str:
    """优先使用会话日期，否则回退到注册日期。"""
    return session.date or (session.registered_at[:10] if session.registered_at else "")


def plan_recalibration(
    root: Path, *, scope_all: bool = False, from_date: str | None = None
) -> RecalibrationPlan:
    """冻结用户会话、完成 offset 与 live 文件摘要。"""
    _validate_scope(scope_all=scope_all, from_date=from_date)
    # 只读连接和禁用迁移共同保证计划阶段不会创建或修改 sessions.db。
    conn = open_sessions_db_readonly(root)
    if conn is None:
        return RecalibrationPlan(
            scope_all=scope_all,
            from_date=from_date,
            sessions=(),
            missing_jsonl=(),
            live_hashes=_live_hashes(root),
            estimated_agent_calls=0,
        )
    try:
        repo = create_sessions_repository(conn, migrate=False)
        records = repo.find_all_completed_sessions(exclude_kinds=_EXCLUDE_KINDS)
    finally:
        conn.close()

    frozen: list[FrozenSession] = []
    missing: list[str] = []
    for rec in records:
        if not scope_all:
            day = _session_day(rec)
            if not day or day < (from_date or ""):
                continue
        end = rec.last_completed_offset or 0
        jsonl_path = rec.jsonl_path or ""
        exists = bool(jsonl_path) and Path(jsonl_path).exists()
        if not exists:
            missing.append(rec.cc_session_id)
        frozen.append(
            FrozenSession(
                cc_session_id=rec.cc_session_id,
                end_offset=end,
                jsonl_path=jsonl_path,
                jsonl_exists=exists,
                registered_at=rec.registered_at,
            )
        )
    return RecalibrationPlan(
        scope_all=scope_all,
        from_date=from_date,
        sessions=tuple(frozen),
        missing_jsonl=tuple(missing),
        live_hashes=_live_hashes(root),
        estimated_agent_calls=sum(1 for s in frozen if s.jsonl_exists),
    )
