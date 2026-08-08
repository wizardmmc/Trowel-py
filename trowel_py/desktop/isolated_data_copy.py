"""为真实 runtime 测试创建不继承运行态所有权的 Desktop 数据副本。"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

_VOLATILE_ROOT_NAMES = frozenset(
    {
        ".data-root.lock",
        "exit-markers",
        "resource-exit.json",
        "resource-lifecycle.json",
        "sidecar-exit.json",
        "codex-accounts",
    }
)
_SQLITE_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3"})


@dataclass(frozen=True)
class IsolatedDataCopyResult:
    """记录隔离副本的去敏数量结果。

    Attributes:
        copied_files: 成功复制的普通文件数。
        sqlite_databases: 通过 SQLite backup API 生成的数据库数。
        skipped_volatile: 已排除的运行态文件或目录数。
    """

    copied_files: int
    sqlite_databases: int
    skipped_volatile: int


def copy_desktop_data_for_isolated_runtime(
    source_root: Path,
    target_root: Path,
) -> IsolatedDataCopyResult:
    """安全复制 Desktop 业务数据，排除快照、锁和 SQLite 临时文件。

    SQLite 文件使用官方 backup API 读取，避免从正在运行的 App 直接拷贝
    主库、WAL 和 SHM 得到不一致副本。资源快照和退出标记不是业务数据，
    隔离 sidecar 不得从它们继承进程终止权限或历史退出事实。

    Args:
        source_root: 已存在的 Desktop 数据根，可以正在被正式 App 读写。
        target_root: 用于测试的新数据根；必须不存在或为空目录。

    Returns:
        普通文件、SQLite 数据库和已排除运行态项的数量。

    Raises:
        ValueError: 源目录不可用、源与目标相同，或目标已含文件。
        sqlite3.Error: 源 SQLite 数据库无法产生一致副本。
    """

    source = source_root.expanduser().resolve()
    target = target_root.expanduser().resolve()
    if not source.is_dir():
        raise ValueError("source Desktop data root is not a directory")
    if source == target:
        raise ValueError("source and target Desktop data roots must differ")
    if target.is_relative_to(source):
        raise ValueError("target Desktop data root must be outside the source root")
    if target.exists() and any(target.iterdir()):
        raise ValueError("target Desktop data root must be empty")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.chmod(0o700)

    copied_files = 0
    sqlite_databases = 0
    skipped_volatile = 0
    for source_path in sorted(source.rglob("*")):
        relative = source_path.relative_to(source)
        if _is_volatile(relative):
            skipped_volatile += 1
            continue
        if source_path.is_symlink():
            skipped_volatile += 1
            continue
        target_path = target / relative
        if source_path.is_dir():
            target_path.mkdir(parents=True, exist_ok=True, mode=0o700)
            target_path.chmod(0o700)
            continue
        if not source_path.is_file():
            skipped_volatile += 1
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if source_path.suffix.lower() in _SQLITE_SUFFIXES:
            _backup_sqlite(source_path, target_path)
            sqlite_databases += 1
        else:
            shutil.copyfile(source_path, target_path)
            target_path.chmod(0o600)
            copied_files += 1
    return IsolatedDataCopyResult(
        copied_files=copied_files,
        sqlite_databases=sqlite_databases,
        skipped_volatile=skipped_volatile,
    )


def _is_volatile(relative_path: Path) -> bool:
    """判断相对路径是否属于不可复制的运行态文件。

    Args:
        relative_path: 相对 Desktop 数据根的文件或目录路径。

    Returns:
        顶层资源所有权文件、任意锁、WAL、SHM 或临时文件返回 True。
    """

    if relative_path.parts and relative_path.parts[0] in _VOLATILE_ROOT_NAMES:
        return True
    name = relative_path.name
    return bool(
        name.endswith(".lock")
        or name.endswith("-wal")
        or name.endswith("-shm")
        or name.endswith("-journal")
        or name.endswith(".tmp")
    )


def _backup_sqlite(source: Path, target: Path) -> None:
    """使用 SQLite backup API 把可能正在写入的数据库复制到新文件。

    Args:
        source: 源 SQLite 数据库文件。
        target: 不存在的目标数据库文件。
    """

    reader = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    writer = sqlite3.connect(target)
    try:
        reader.backup(writer)
    finally:
        writer.close()
        reader.close()
    target.chmod(0o600)


def main(argv: Sequence[str] | None = None) -> int:
    """解析源和目标数据根，生成一份安全隔离副本。

    Args:
        argv: 命令行参数；为 None 时使用当前进程参数。

    Returns:
        复制完成时返回 0。
    """

    parser = argparse.ArgumentParser(
        description="为真实 runtime 测试生成不含运行态所有权的 Desktop 数据副本"
    )
    parser.add_argument("source", type=Path, help="源 Desktop 数据根")
    parser.add_argument("target", type=Path, help="空的隔离目标数据根")
    arguments = parser.parse_args(argv)
    result = copy_desktop_data_for_isolated_runtime(
        arguments.source,
        arguments.target,
    )
    print(
        f"copied_files={result.copied_files} "
        f"sqlite_databases={result.sqlite_databases} "
        f"skipped_volatile={result.skipped_volatile}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
