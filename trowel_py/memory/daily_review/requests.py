"""持久登记用户会话关闭后需要立即处理的 Memory review 请求。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from trowel_py.agent_host.binding import SessionBinding
from trowel_py.memory.sessions_repo import (
    ReviewRequest,
    create_sessions_repository,
    open_sessions_db,
)

NowFn = Callable[[], datetime]


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
    conn = open_sessions_db(memory_root)
    try:
        create_sessions_repository(conn).review_requests.enqueue(
            binding.session_id,
            runtime=binding.runtime.value,
            requested_at=now.isoformat(timespec="microseconds"),
            expected_native_session_id=(
                binding.native_session_id
                if binding.runtime.value == "claude_code"
                and binding.native_session_id
                else None
            ),
        )
    finally:
        conn.close()


def load_session_review_requests(memory_root: Path) -> list[ReviewRequest]:
    """读取全部尚未完成的会话关闭 review 请求。"""

    conn = open_sessions_db(memory_root)
    try:
        return create_sessions_repository(conn).review_requests.list_pending()
    finally:
        conn.close()
