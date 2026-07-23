"""隔离 CC host 测试对真实 memory 元数据和 MCP 配置的写入。

每项测试先把 memory root 与 MCP 配置重定向到临时目录，再用只读快照确认真实
sessions.db 未变化。两层保护必须同时保留，避免未来代码绕过其中一层。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

_REAL_DB = Path.home() / ".trowel" / "memory" / "meta" / "sessions.db"


def _snapshot_real_sessions_db() -> tuple[int, int | None] | None:
    if not _REAL_DB.exists():
        return None
    mtime = _REAL_DB.stat().st_mtime_ns
    count: int | None
    try:
        conn = sqlite3.connect(f"file:{_REAL_DB}?mode=ro", uri=True)
        try:
            count = conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        count = None
    return (mtime, count)


@pytest.fixture(autouse=True)
def _isolate_and_guard_memory_db(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> object:
    mem = tmp_path / "memory"
    monkeypatch.setattr(
        "trowel_py.memory.paths.resolve_memory_root",
        lambda config_path=None: mem,
    )
    # memory-on route 会调用 write_mcp_config，必须同步重定向以免污染真实 home。
    monkeypatch.setenv("TROWEL_MCP_CONFIG", str(tmp_path / "memory-mcp-config.json"))
    # checkpoint 默认因隐私关闭；测试套件显式开启后才会覆盖真实 ref 创建路径。
    monkeypatch.setenv("TROWEL_CHECKPOINT_ENABLE", "1")
    before = _snapshot_real_sessions_db()
    yield
    after = _snapshot_real_sessions_db()
    assert before == after, (
        f"cc_host test mutated the real sessions.db: {before} -> {after}"
    )
