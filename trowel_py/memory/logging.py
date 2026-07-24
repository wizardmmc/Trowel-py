"""早期原始访问与会话结果日志的兼容接口。

该接口只记录原始事实，不预先分类。它与 :mod:`trowel_py.memory.access_log`
复用文件名但使用不同 schema，调用方不得在同一 Memory 根目录混用两套写入
接口。
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
    """一次笔记正文打开事件。"""

    note_id: str
    when: str
    context_ref: str = ""


@dataclass(frozen=True)
class OutcomeRecord:
    """未分类的会话结果事实。"""

    session_ref: str
    when: str
    retry_count: int = 0
    corrections: int = 0
    transcript_ref: str = ""


def log_note_access(
    root: Path | str, note_id: str, when: str, context_ref: str = ""
) -> None:
    """追加一条笔记打开记录。"""
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
    """追加一条不含分类标签的会话结果记录。"""
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
    """按追加顺序读取访问记录；文件不存在时返回空列表。"""
    return _read(Path(root) / _META_DIR / _ACCESS_LOG, AccessRecord)


def read_outcome_log(root: Path | str) -> list[OutcomeRecord]:
    """按追加顺序读取结果记录；文件不存在时返回空列表。"""
    return _read(Path(root) / _META_DIR / _OUTCOME_LOG, OutcomeRecord)


def _append(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _read(path: Path, cls: type) -> list[Any]:
    """按追加顺序返回可解码且字段兼容的记录。"""
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
