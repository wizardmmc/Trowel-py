"""提供旧版原始访问与会话结果日志的兼容读写。

该接口只记录原始事实，不预先分类。它与 :mod:`trowel_py.memory.access_log`
复用日志文件名，但两者的记录字段不同，不得在同一 Memory 根目录混用写入
接口。两个记录类不校验字段值类型；读取时只要求每行内容能解析为 JSON，并能
按字段名构造相应记录。

两种写入函数都不加锁，也不保证并发写入时每条记录仍保持完整一行；同一日志
文件应由调用方串行写入。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_META_DIR = "meta"
_ACCESS_LOG = "access-log.jsonl"
_OUTCOME_LOG = "outcome-log.jsonl"


@dataclass(frozen=True)
class AccessRecord:
    """记录一次笔记正文打开事件。

    Attributes:
        note_id: 被打开笔记的文件 stem，不含扩展名。
        when: 打开时间或调用方提供的轮次标识。
        context_ref: 触发本次打开的会话或轮次标识；未提供时为空字符串。
    """

    note_id: str
    when: str
    context_ref: str = ""


@dataclass(frozen=True)
class OutcomeRecord:
    """记录一条尚未分类的会话结果事实。

    Attributes:
        session_ref: 产生结果的会话标识。
        when: 结果发生时间。
        retry_count: 会话中的重试次数；未记录时为 0，不校验范围。
        corrections: 用户纠正模型的次数；未记录时为 0，不校验范围。
        transcript_ref: 完整会话正文的位置；未记录时为空字符串。
    """

    session_ref: str
    when: str
    retry_count: int = 0
    corrections: int = 0
    transcript_ref: str = ""


def log_note_access(
    root: Path | str, note_id: str, when: str, context_ref: str = ""
) -> None:
    """向旧版访问日志追加一条笔记正文打开记录。

    Args:
        root: 日志所在的 Memory 根目录。
        note_id: 被打开笔记的文件 stem，不含扩展名。
        when: 打开时间或调用方提供的轮次标识。
        context_ref: 触发本次打开的会话或轮次标识；未知时留空。
    """
    _append(
        Path(root) / _META_DIR / _ACCESS_LOG,
        asdict(AccessRecord(note_id=note_id, when=when, context_ref=context_ref)),
    )


def log_session_outcome(
    root: Path | str,
    session_ref: str,
    when: str,
    *,
    retry_count: int = 0,
    corrections: int = 0,
    transcript_ref: str = "",
) -> None:
    """向旧版结果日志追加一条不含分类标签的会话事实。

    Args:
        root: 日志所在的 Memory 根目录。
        session_ref: 产生结果的会话标识。
        when: 结果发生时间。
        retry_count: 会话中的重试次数。
        corrections: 用户纠正模型的次数。
        transcript_ref: 完整会话正文的位置；未知时留空。
    """
    _append(
        Path(root) / _META_DIR / _OUTCOME_LOG,
        asdict(
            OutcomeRecord(
                session_ref=session_ref,
                when=when,
                retry_count=retry_count,
                corrections=corrections,
                transcript_ref=transcript_ref,
            )
        ),
    )


def read_access_log(root: Path | str) -> list[AccessRecord]:
    """按追加顺序读取旧版访问日志中的有效记录。

    空行、无效 JSON、非对象和缺失或多余字段的记录会被跳过；字段值类型不做
    校验。文件不存在时返回空列表。

    Args:
        root: 日志所在的 Memory 根目录。

    Returns:
        日志中可解码的笔记正文打开记录。
    """
    return _read(Path(root) / _META_DIR / _ACCESS_LOG, AccessRecord)


def read_outcome_log(root: Path | str) -> list[OutcomeRecord]:
    """按追加顺序读取旧版结果日志中的有效记录。

    空行、无效 JSON、非对象和缺失或多余字段的记录会被跳过；字段值类型不做
    校验。文件不存在时返回空列表。

    Args:
        root: 日志所在的 Memory 根目录。

    Returns:
        日志中可解码且未经分类的会话结果事实。
    """
    return _read(Path(root) / _META_DIR / _OUTCOME_LOG, OutcomeRecord)


def _append(path: Path, obj: dict[str, Any]) -> None:
    """把一条原始事实编码为 JSON 行并追加到指定文件。

    父目录会按需创建。函数每次单独打开文件，不加锁、不调用 ``fsync``，也不
    保证并发写入仍保持完整行；编码和 I/O 异常直接传播。

    Args:
        path: 目标 JSONL 文件路径；父目录不存在时会创建。
        obj: 要编码并写入的原始事实。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _read(path: Path, cls: type) -> list[Any]:
    """把日志中可解码且能作为关键字参数构造的 JSON 行转换为记录。

    空行会被忽略；无效 JSON 会告警并跳过。``cls(**obj)`` 触发的任何
    ``TypeError`` 也会告警并跳过；对本模块的两种记录而言，这通常代表非对象
    或缺失、多余字段。本模块的两种记录类不校验字段值类型；传入其他记录类时，
    其构造过程中抛出的非 ``TypeError`` 异常会直接传播。函数一次读取整个
    UTF-8 文件，读取或解码失败会直接传播。

    Args:
        path: 要读取的 JSONL 文件路径。
        cls: 接收单行 JSON 对象字段的记录类。

    Returns:
        按日志追加顺序排列的有效记录对象；文件不存在时为空列表。
    """
    if not path.exists():
        return []
    out: list[Any] = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines()):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("skipping corrupt %s line %d: %r", path.name, i, line[:80])
            continue
        try:
            out.append(cls(**obj))
        except TypeError:
            logger.warning("skipping malformed %s line %d (missing keys)", path.name, i)
    return out
