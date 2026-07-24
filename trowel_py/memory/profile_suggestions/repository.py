"""画像建议队列的文件与锁实现。"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Protocol

from trowel_py.memory.types import Suggestion


class _FileLock(Protocol):
    LOCK_EX: int
    LOCK_UN: int

    def flock(self, fd: int, operation: int) -> object: ...


@contextlib.contextmanager
def suggestions_lock(
    root: Path,
    *,
    meta_dir: str,
    file_lock: _FileLock | None,
    open_file: Callable[[str, int], int],
    close_file: Callable[[int], None],
    create_flag: int,
    read_write_flag: int,
) -> Iterator[None]:
    """在支持 flock 的平台锁住队列读改写周期。"""
    if file_lock is None:
        yield
        return

    lock_path = root / meta_dir / ".suggestions.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = open_file(str(lock_path), create_flag | read_write_flag)
    try:
        file_lock.flock(fd, file_lock.LOCK_EX)
        yield
    finally:
        file_lock.flock(fd, file_lock.LOCK_UN)
        close_file(fd)


def queue_path(root: Path, *, meta_dir: str, filename: str) -> Path:
    """组装建议队列路径。"""
    return root / meta_dir / filename


def load_queue(
    path: Path,
    *,
    decode: Callable[[dict[str, object]], Suggestion],
    loads: Callable[[str], object],
    decode_error: type[Exception],
) -> tuple[list[Suggestion], str]:
    """读取队列；损坏的 JSON 或记录由调用方显式处理。"""
    if not path.exists():
        return [], ""
    try:
        data = loads(path.read_text(encoding="utf-8"))
    except decode_error as exc:
        raise ValueError(f"corrupt suggestion queue at {path}: {exc}") from exc

    raw = data.get("suggestions", []) if isinstance(data, dict) else []
    updated = str(data.get("updated", "")) if isinstance(data, dict) else ""
    items = [decode(item) for item in raw if isinstance(item, dict)]
    return items, updated


def write_queue(
    path: Path,
    items: Sequence[Suggestion],
    *,
    updated: str,
    encode: Callable[[Suggestion], dict[str, object]],
    dumps: Callable[..., str],
) -> None:
    """以稳定 JSON 格式覆盖写入完整队列。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "suggestions": [encode(item) for item in items],
        "updated": updated,
    }
    path.write_text(
        dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
