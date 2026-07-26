"""以工作目录句柄为边界安全打开会话文件。"""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO


class LocalFileError(Exception):
    """会话文件请求无法安全完成。"""


class InvalidLocalFilePath(LocalFileError):
    """请求路径不是可接受的项目相对路径。"""


class LocalFileAccessError(LocalFileError):
    """请求可能越过工作目录或当前平台无法保证边界。"""


class LocalFileNotFoundError(LocalFileError):
    """目标不存在或不是普通文件。"""


def open_local_file(workdir: str, path: str) -> BinaryIO:
    """逐级禁止 symlink，并返回已锚定到目标 inode 的只读文件。"""

    relative = Path(path)
    if relative.is_absolute() or not relative.parts:
        raise InvalidLocalFilePath("path must be relative")
    if ".." in relative.parts:
        raise LocalFileAccessError("path escapes session workdir")
    required_flags = ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")
    if os.open not in os.supports_dir_fd or not all(
        hasattr(os, name) for name in required_flags
    ):
        raise LocalFileAccessError("safe local file access is unsupported")

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    root_fd: int | None = None
    current_fd: int | None = None
    file_fd: int | None = None
    try:
        root_fd = os.open(Path(workdir).resolve(), directory_flags)
        current_fd = root_fd
        for part in relative.parts[:-1]:
            next_fd = os.open(part, directory_flags, dir_fd=current_fd)
            if current_fd != root_fd:
                os.close(current_fd)
            current_fd = next_fd
        file_fd = os.open(relative.parts[-1], file_flags, dir_fd=current_fd)
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise LocalFileNotFoundError("file not found")
        handle = os.fdopen(file_fd, "rb")
        file_fd = None
        return handle
    except LocalFileError:
        raise
    except FileNotFoundError as exc:
        raise LocalFileNotFoundError("file not found") from exc
    except (NotADirectoryError, PermissionError) as exc:
        raise LocalFileAccessError("path escapes session workdir") from exc
    except ValueError as exc:
        raise InvalidLocalFilePath("invalid path") from exc
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR, errno.EACCES, errno.EPERM}:
            raise LocalFileAccessError("path escapes session workdir") from exc
        if exc.errno == errno.ENOENT:
            raise LocalFileNotFoundError("file not found") from exc
        raise InvalidLocalFilePath("invalid path") from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if current_fd is not None and current_fd != root_fd:
            os.close(current_fd)
        if root_fd is not None:
            os.close(root_fd)


def iter_file_chunks(handle: BinaryIO, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
    try:
        while chunk := handle.read(chunk_size):
            yield chunk
    finally:
        handle.close()
