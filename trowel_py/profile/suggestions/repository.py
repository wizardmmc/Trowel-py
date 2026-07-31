"""提供可注入依赖的画像建议队列路径、文件锁和整队列读写原语。"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Protocol

from trowel_py.profile.models import Suggestion


class _FileLock(Protocol):
    """描述建议队列独占锁所需的最小接口。

    Attributes:
        LOCK_EX: 传给 ``flock`` 的独占锁操作值。
        LOCK_UN: 传给 ``flock`` 的解锁操作值。
    """

    LOCK_EX: int
    LOCK_UN: int

    def flock(self, fd: int, operation: int) -> object:
        """对文件描述符执行调用方指定的锁操作。

        Args:
            fd: 已打开的锁文件描述符。
            operation: ``LOCK_EX`` 或 ``LOCK_UN``。

        Returns:
            底层锁实现的返回值；调用方忽略该值。
        """
        ...


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
    """用注入的 ``flock`` 接口包围一个队列读改写周期。

    ``file_lock`` 为 ``None`` 时直接进入上下文，不创建目录或调用其他依赖。
    否则创建 ``<root>/<meta_dir>/.suggestions.lock`` 的父目录，以两个标志的
    按位或打开锁文件，并在进入调用方代码前申请独占锁。文件打开后，无论
    独占加锁或调用方代码是否失败，都会先尝试解锁，解锁成功后再关闭描述符；
    解锁失败会跳过关闭，解锁或关闭异常可能覆盖此前异常。注入的打开、锁和
    关闭函数所抛异常均原样传播。

    Args:
        root: Memory 根目录。
        meta_dir: 锁文件所在的元数据目录名或路径。
        file_lock: 提供独占锁和解锁操作的对象；``None`` 表示禁用锁。
        open_file: 按路径文本和整数标志打开锁文件的函数。
        close_file: 关闭锁文件描述符的函数。
        create_flag: 创建文件的打开标志。
        read_write_flag: 读写打开标志。

    Yields:
        调用方的执行区间；启用锁时，进入该区间前已获得独占锁。

    Raises:
        OSError: 目录操作或注入的文件、锁操作抛出 ``OSError``。
    """
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
    """按 ``Path`` 拼接规则组装建议队列路径。

    函数不校验目录名和文件名；绝对 ``meta_dir`` 或 ``filename`` 会丢弃前面
    已拼接的路径部分。

    Args:
        root: Memory 根目录。
        meta_dir: 队列文件所在的元数据目录名或路径。
        filename: 队列文件名或路径。

    Returns:
        ``root / meta_dir / filename`` 的结果。
    """
    return root / meta_dir / filename


def load_queue(
    path: Path,
    *,
    decode: Callable[[dict[str, object]], Suggestion],
    loads: Callable[[str], object],
    decode_error: type[Exception],
) -> tuple[list[Suggestion], str]:
    """读取文本、解析顶层队列并解码其中的对象条目。

    路径不存在时返回空列表和空时间。解析结果不是字典时也返回空值；字典缺少
    suggestions 时按空列表处理，非字典条目静默跳过。``updated`` 缺失时取
    空字符串，存在时直接字符串化。函数不获取文件锁。

    路径存在性检查位于异常包装之外，其异常原样传播。``read_text`` 或
    ``loads`` 抛出的 ``decode_error`` 会包装为 ``ValueError``；``decode``
    及其他依赖异常原样传播。

    Args:
        path: 队列文件路径。
        decode: 把单条字典记录转换为建议的函数。
        loads: 把 UTF-8 文本解析为 Python 值的函数。
        decode_error: 需要转换为队列损坏错误的异常类型。

    Returns:
        按输入顺序解码的建议新列表和更新时间文本。

    Raises:
        OSError: 路径存在性检查失败，或读取抛出的异常不属于
            ``decode_error``。
        UnicodeDecodeError: 文件不是有效的 UTF-8，且异常不属于
            ``decode_error``。
        ValueError: 读取或解析抛出了 ``decode_error``。
        TypeError: suggestions 存在但不可迭代。
    """
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
    """编码完整队列并以调用方提供的 JSON 函数覆盖写入。

    函数先创建父目录，再按输入顺序编码所有建议，最后以
    ``ensure_ascii=False`` 和 ``indent=2`` 调用 ``dumps``。写入不获取锁，
    也不使用临时文件原子替换；是否带末尾换行由 ``dumps`` 返回文本决定。
    ``encode`` 和 ``dumps`` 抛出的异常原样传播。

    Args:
        path: 队列文件路径。
        items: 按目标磁盘顺序写入的建议。
        updated: 原样放入顶层 ``updated`` 字段的值。
        encode: 把单条建议转换为字典的函数。
        dumps: 接受 payload、``ensure_ascii`` 和 ``indent`` 的序列化函数。

    Raises:
        OSError: 无法创建父目录或写入文件。
        TypeError: 注入依赖原样抛出，或 ``dumps`` 返回值不被 ``write_text``
            接受。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "suggestions": [encode(item) for item in items],
        "updated": updated,
    }
    path.write_text(
        dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
