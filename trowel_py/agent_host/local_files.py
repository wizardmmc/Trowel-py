"""安全读取会话所使用项目目录中的文件，供文件下载接口分块返回。

只接受项目目录内的相对路径，并拒绝上级目录 ``..``、符号链接以及目录、管道、
设备文件等非普通文件。文件打开后固定到当时的文件本身，避免路径随后被替换到
项目目录之外；当前平台无法可靠保证这些限制时也会拒绝读取。
"""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO


class LocalFileError(Exception):
    """表示读取会话所用项目目录中的文件失败。"""


class InvalidLocalFilePath(LocalFileError):
    """表示请求路径为空、是绝对路径或格式无法处理。"""


class LocalFileAccessError(LocalFileError):
    """表示请求可能访问项目目录之外的文件，或当前系统无法保证访问范围。"""


class LocalFileNotFoundError(LocalFileError):
    """表示目标不存在，或不是允许读取的普通文件。"""


def open_local_file(workdir: str, path: str) -> BinaryIO:
    """以只读二进制方式打开会话项目目录中的一个普通文件。

    path 必须是项目目录内的相对路径，不能包含 ``..``，请求路径中的任何一级也
    不能是符号链接。文件打开后，即使同一路径随后被替换，返回的文件仍是原先打开
    的那个文件。

    Args:
        workdir: 会话所使用的项目目录。
        path: 相对于项目目录的文件路径。

    Returns:
        已打开的只读二进制文件；调用方负责在使用后关闭。

    Raises:
        InvalidLocalFilePath: 路径为空、是绝对路径或格式无法处理。
        LocalFileAccessError: 请求可能越过项目目录，或当前系统无法安全打开文件。
        LocalFileNotFoundError: 目标不存在，或不是允许读取的普通文件。
    """

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
    """按指定大小分块读取二进制文件，并负责关闭文件。

    正常读取完成、读取出错或迭代器被关闭时，都会关闭文件。

    Args:
        handle: 要读取的二进制文件，传入后由本函数负责关闭。
        chunk_size: 每次从文件中读取的字节数，默认 64 KiB。

    Yields:
        按文件顺序读出的字节块。
    """
    try:
        while chunk := handle.read(chunk_size):
            yield chunk
    finally:
        handle.close()
