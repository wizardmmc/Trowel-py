"""持久登记用户会话关闭后需要立即处理的 Memory review 请求。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from trowel_py.agent_host.binding import SessionBinding
from trowel_py.memory.sessions_repo import (
    ReviewRequest,
    create_sessions_repository,
    open_sessions_db,
)

NowFn = Callable[[], datetime]
SESSION_REVIEW_DELAY = timedelta(minutes=5)


def enqueue_session_review(
    memory_root: Path,
    binding: SessionBinding,
    *,
    now_fn: NowFn | None = None,
) -> None:
    """在会话 binding 删除前持久登记一次即时 review。

    Args:
        memory_root: 保存会话水位和待处理请求的 Memory 根目录。
        binding: 即将关闭的用户会话绑定；会话 ID 作为幂等键，runtime 决定
            后续查询 Claude Code 片段还是 Codex turns。
        now_fn: 生成请求时间的可选时钟；未提供时使用本地当前时间。
    """

    now = (now_fn or datetime.now)()
    closed_at = (
        now.isoformat(timespec="microseconds")
        if now.tzinfo is not None and now.utcoffset() is not None
        else now.astimezone().isoformat(timespec="microseconds")
    )
    wall_clock_now = now.replace(tzinfo=None) if now.tzinfo is not None else now
    conn = open_sessions_db(memory_root)
    try:
        create_sessions_repository(conn).review_requests.enqueue(
            binding.session_id,
            runtime=binding.runtime.value,
            requested_at=wall_clock_now.isoformat(timespec="microseconds"),
            not_before=(wall_clock_now + SESSION_REVIEW_DELAY).isoformat(
                timespec="microseconds"
            ),
            closed_at=closed_at,
            expected_native_session_id=(
                binding.native_session_id
                if binding.runtime.value == "claude_code" and binding.native_session_id
                else None
            ),
        )
    finally:
        conn.close()


def load_session_review_requests(
    memory_root: Path,
    *,
    eligible_at: str | None = None,
) -> list[ReviewRequest]:
    """读取全部或已经到期的会话关闭 review 请求。

    Args:
        memory_root: 保存请求队列的 Memory 根目录。
        eligible_at: 只读取不晚于该本地 ISO 时间的请求；None 表示读取全部。
    """

    conn = open_sessions_db(memory_root)
    try:
        return create_sessions_repository(conn).review_requests.list_pending(
            eligible_at=eligible_at
        )
    finally:
        conn.close()
