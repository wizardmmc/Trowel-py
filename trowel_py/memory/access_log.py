"""在线读取链路的追加式访问与结果日志。

日志是 north-star 指标的事实源。CC 写入时保留 ``cc_session_id`` 和
``toolUseId``，Codex 没有对应的逐调用标识；跨 host 身份由 ``host_kind`` 与
``native_session_id`` 表达。读取会跳过损坏或字段不兼容的行，避免单行故障
使其余历史不可用。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

_META_DIR = "meta"
_ACCESS_LOG = "access-log.jsonl"
_OUTCOME_LOG = "outcome-log.jsonl"

Action = Literal["search", "read"]
Outcome = Literal["helpful", "harmful", "unused", "unknown"]


@dataclass(frozen=True)
class AccessRecord:
    """一次搜索或正文读取；读取记录以 ``search_id`` 关联来源搜索。

    ``toolUseId`` 沿用 CC 协议字段名。Codex 记录的 CC 专属字段为空；旧记录
    缺少跨 host 身份字段时由默认空值兼容。
    """

    ts: str
    trowel_session_id: str
    cc_session_id: str
    toolUseId: str
    action: Action
    search_id: str
    read_id: str = ""
    query: str = ""
    memory_id: str = ""
    rank: int | None = None
    host_kind: str = ""
    native_session_id: str = ""


@dataclass(frozen=True)
class OutcomeRecord:
    """一次正文读取后的模型反馈，身份字段与 :class:`AccessRecord` 一致。"""

    ts: str
    trowel_session_id: str
    cc_session_id: str
    toolUseId: str
    read_id: str
    memory_id: str
    outcome: Outcome
    reason: str = ""
    host_kind: str = ""
    native_session_id: str = ""


def log_access(root: Path | str, rec: AccessRecord) -> None:
    """向 ``meta/access-log.jsonl`` 追加一条访问记录。"""
    _append(Path(root) / _META_DIR / _ACCESS_LOG, asdict(rec))


def log_outcome(root: Path | str, rec: OutcomeRecord) -> None:
    """向 ``meta/outcome-log.jsonl`` 追加一条结果记录。"""
    _append(Path(root) / _META_DIR / _OUTCOME_LOG, asdict(rec))


def read_access_log(root: Path | str) -> list[AccessRecord]:
    """按追加顺序读取访问记录；文件不存在时返回空列表。"""
    return _read(Path(root) / _META_DIR / _ACCESS_LOG, AccessRecord)


def read_outcome_log(root: Path | str) -> list[OutcomeRecord]:
    """按追加顺序读取结果记录；文件不存在时返回空列表。"""
    return _read(Path(root) / _META_DIR / _OUTCOME_LOG, OutcomeRecord)


def _append(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _read(path: Path, cls: type) -> list:
    """按追加顺序返回可解码且字段兼容的记录。"""
    if not path.exists():
        return []
    out: list = []
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
