"""只读生成画像重校准范围、会话元数据和 live 文件摘要。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db_readonly,
)

from trowel_py.profile.recalibration.models import (
    _LIVE_PROFILE,
    _LIVE_SUGGESTIONS,
    _LIVE_WATERMARK,
    FrozenSession,
    LiveHashes,
    RecalibrationPlan,
    RecalibrationScopeError,
)


def _validate_scope(*, scope_all: bool, from_date: str | None) -> None:
    """要求且仅允许 all 或 from 一种重放范围。

    ``from_date`` 只按是否为 ``None`` 判断是否指定，空字符串也算 from 范围；
    本函数不校验日期格式。

    Args:
        scope_all: 是否选择 all 范围。
        from_date: from 范围的起始日期文本。

    Raises:
        RecalibrationScopeError: 两种范围同时指定或均未指定。
    """
    if scope_all and from_date is not None:
        raise RecalibrationScopeError("specify either --all or --from, not both")
    if not scope_all and from_date is None:
        raise RecalibrationScopeError("must specify --all or --from")


def _sha256_file(path: Path) -> str | None:
    """读取整个文件并计算 SHA-256 摘要。

    Args:
        path: 要读取的文件路径。

    Returns:
        64 位十六进制摘要；路径不存在时为 ``None``。

    Raises:
        OSError: 路径存在但无法作为文件读取，或读取期间发生 I/O 错误。
    """
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _live_hashes(root: Path) -> LiveHashes:
    """依次计算 Profile、建议队列和独立水位文件的 SHA-256 摘要。

    三个文件没有加锁，也不是原子快照；并发写入可能让返回值来自不同时间点。

    Args:
        root: 三个 live 文件所在的 Memory 根目录。

    Returns:
        各文件的摘要；缺失文件对应 ``None``。
    """
    return LiveHashes(
        profile=_sha256_file(root / _LIVE_PROFILE[1]),
        suggestions=_sha256_file(root / _LIVE_SUGGESTIONS[1]),
        watermark=_sha256_file(root / _LIVE_WATERMARK[1]),
    )


def _session_day(session: SessionRecord) -> str:
    """选择 from 范围比较使用的会话日期文本。

    优先原样返回非空 ``session.date``；否则取 ``registered_at`` 的前 10 个
    字符。两者均为空时返回空字符串，不解析或校验日期。

    Args:
        session: 提供日期和注册时间的会话记录。

    Returns:
        用于字符串比较的日期文本。
    """
    return session.date or (session.registered_at[:10] if session.registered_at else "")


def plan_recalibration(
    root: Path, *, scope_all: bool = False, from_date: str | None = None
) -> RecalibrationPlan:
    """只读冻结重校准范围内的用户会话和 live 文件摘要。

    范围校验后，以只读且禁用迁移的方式打开现有 sessions 数据库；数据库缺失
    时返回空会话计划，不创建目录或文件。已有数据库必须具备当前查询所需
    schema，本函数不会升级它。候选沿用 repository 默认规则，只包含 eligible、
    已完成的用户会话，并保持注册时间顺序。

    from 范围使用会话日期文本与 ``from_date`` 做字典序比较，包含等于起始值的
    会话；没有日期的会话跳过，不校验日期格式。每个入选会话冻结完成 offset、
    JSONL 路径及当时的 ``Path.exists()`` 结果：空路径计为缺失，相对路径相对
    当前工作目录解释，目录也会被视为存在。JSONL 内容不会复制或哈希。

    sessions 数据库关闭后才逐个计算三个 live 文件摘要，整个计划不是跨数据库
    和文件的原子快照。

    Args:
        root: sessions 数据库和 live Profile 文件所在的 Memory 根目录。
        scope_all: 是否选择全部候选会话。
        from_date: 可选的起始日期文本；使用 from 范围时必须不是 ``None``。

    Returns:
        冻结会话、缺失来源、live 摘要和预计 agent 调用数的只读计划。

    Raises:
        RecalibrationScopeError: all 和 from 范围同时指定或均未指定。
        OSError: 无法检查数据库或 JSONL 路径，或无法读取 live 文件。
        sqlite3.Error: sessions 数据库无法只读打开或查询。
    """
    _validate_scope(scope_all=scope_all, from_date=from_date)
    # 只读连接和禁用迁移共同保证计划阶段不会创建或升级 sessions.db。
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
        records = repo.claude.find_all_completed_sessions()
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
