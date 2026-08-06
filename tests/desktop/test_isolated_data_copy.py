"""验证真实 runtime 测试副本不继承运行态文件或不一致 SQLite 旁路文件。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from trowel_py.desktop.isolated_data_copy import (
    copy_desktop_data_for_isolated_runtime,
)


def test_isolated_copy_excludes_process_ownership_and_uses_sqlite_backup(
    tmp_path: Path,
) -> None:
    """副本保留业务数据，但不包含 PID 快照、退出事实、锁和 WAL/SHM。"""

    source = tmp_path / "canonical"
    target = tmp_path / "isolated"
    source.mkdir()
    (source / "config.toml").write_text("[memory]\n", encoding="utf-8")
    (source / "resource-lifecycle.json").write_text('{"pid":501}', encoding="utf-8")
    (source / "resource-exit.json").write_text("{}", encoding="utf-8")
    (source / ".data-root.lock").write_text("lock", encoding="utf-8")
    (source / "secondary.sqlite-wal").write_text("wal", encoding="utf-8")
    (source / "secondary.sqlite-shm").write_text("shm", encoding="utf-8")
    (source / "legacy.sqlite-journal").write_text("journal", encoding="utf-8")
    (source / "exit-markers").mkdir()
    (source / "exit-markers" / "old.json").write_text("{}", encoding="utf-8")
    account = source / "codex-accounts" / "official-1"
    account.mkdir(parents=True)
    (account / "auth.json").write_text("oauth-canary", encoding="utf-8")
    database = sqlite3.connect(source / "trowel.db")
    database.execute("PRAGMA journal_mode=WAL")
    database.execute("CREATE TABLE marker(value TEXT)")
    database.execute("INSERT INTO marker(value) VALUES ('business-data')")
    database.commit()

    try:
        result = copy_desktop_data_for_isolated_runtime(source, target)
    finally:
        database.close()

    copied = sqlite3.connect(target / "trowel.db")
    try:
        value = copied.execute("SELECT value FROM marker").fetchone()[0]
    finally:
        copied.close()
    assert value == "business-data"
    assert (target / "config.toml").read_text(encoding="utf-8") == "[memory]\n"
    assert not (target / "resource-lifecycle.json").exists()
    assert not (target / "resource-exit.json").exists()
    assert not (target / ".data-root.lock").exists()
    assert not (target / "exit-markers").exists()
    assert not (target / "codex-accounts").exists()
    assert not (target / "trowel.db-wal").exists()
    assert not (target / "trowel.db-shm").exists()
    assert not (target / "secondary.sqlite-wal").exists()
    assert not (target / "secondary.sqlite-shm").exists()
    assert not (target / "legacy.sqlite-journal").exists()
    assert result.sqlite_databases == 1
    assert result.skipped_volatile >= 4


def test_isolated_copy_rejects_nonempty_target(tmp_path: Path) -> None:
    """拒绝覆盖已有目标，避免混合两个数据根的所有权。"""

    source = tmp_path / "canonical"
    target = tmp_path / "isolated"
    source.mkdir()
    target.mkdir()
    (target / "existing.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="must be empty"):
        copy_desktop_data_for_isolated_runtime(source, target)

    assert (target / "existing.txt").read_text(encoding="utf-8") == "keep"


def test_isolated_copy_rejects_target_nested_in_source(tmp_path: Path) -> None:
    """拒绝递归复制到源目录内部，避免副本再次成为待复制输入。"""

    source = tmp_path / "canonical"
    source.mkdir()

    with pytest.raises(ValueError, match="outside the source"):
        copy_desktop_data_for_isolated_runtime(source, source / "isolated")

    assert not (source / "isolated").exists()
