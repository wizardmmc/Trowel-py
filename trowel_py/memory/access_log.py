"""记录在线检索、正文读取以及读取后的反馈。

日志用于判断笔记是否真正发挥作用，并统计记忆使用质量。记录还可保存实际运行端
及其原生会话 ID；环境未提供或旧记录缺少这些字段时，相应值为空。读取时会跳过
损坏或字段不兼容的行，避免单行故障使其余历史不可用。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar

_RecordT = TypeVar("_RecordT")

logger = logging.getLogger(__name__)

_META_DIR = "meta"
_ACCESS_LOG = "access-log.jsonl"
_OUTCOME_LOG = "outcome-log.jsonl"

Action = Literal["search", "read"]
Outcome = Literal["helpful", "harmful", "unused", "unknown"]


@dataclass(frozen=True)
class AccessRecord:
    """记录一次搜索调用、搜索命中或正文读取。

    搜索时先写入带查询文本的调用记录，再为每条返回笔记写入带排名的命中记录。
    正文读取会保留调用方传入的 ``search_id``，未提供时为空；每次成功读取都会
    分配 ``read_id``，供后续反馈引用。

    Attributes:
        ts: 操作发生时间；MCP 默认写入 UTC 的 ISO 8601 时间。
        trowel_session_id: Trowel 为当前 Agent 会话分配的 ID；环境未提供时为空。
        cc_session_id: 为兼容旧日志保留的 Claude Code 会话 ID；Codex 记录或
            Claude Code 尚未报告会话 ID 时为空。
        toolUseId: 本次搜索或读取调用在 Claude Code 中的工具调用 ID；字段名
            沿用其协议，Codex 记录或宿主未提供时为空。
        action: 记录类型，``search`` 表示搜索调用或命中，``read`` 表示正文读取。
        search_id: 搜索调用和命中共享的 ID；读取记录保留来源搜索 ID，调用方
            未提供时为空。
        read_id: 一次正文读取的 ID；搜索记录为空。
        query: 本次搜索的查询文本；搜索命中和读取记录为空。
        memory_id: 搜索命中或读取的笔记文件名去掉 ``.md`` 后的部分；搜索调用
            记录为空。该值不是 ``Note.memory_id``。
        rank: 笔记在检索器候选中的零起始排名；其他记录为 None。
        host_kind: 实际运行端，当前为 ``cc`` 或 ``codex``；环境未提供或旧记录
            缺少该字段时为空。
        native_session_id: Claude Code 会话 ID 或 Codex thread ID；原生会话
            尚未生成或旧记录缺少该字段时为空。
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
    """记录模型对一次正文读取的反馈。

    Attributes:
        ts: 反馈发生时间；MCP 默认写入 UTC 的 ISO 8601 时间。
        trowel_session_id: Trowel 为反馈所在 Agent 会话分配的 ID；环境未提供时为空。
        cc_session_id: 为兼容旧日志保留的 Claude Code 会话 ID；Codex 记录或
            Claude Code 尚未报告会话 ID 时为空。
        toolUseId: 本次反馈调用在 Claude Code 中的工具调用 ID；字段名沿用其
            协议，Codex 记录或宿主未提供时为空。
        read_id: 被评价的正文读取 ID。
        memory_id: 被评价笔记的文件名去掉 ``.md`` 后的部分，从对应的读取记录
            取得。该值不是 ``Note.memory_id``。
        outcome: ``helpful`` 表示内容已用于决策且产生帮助，``harmful`` 表示
            内容已用于决策但造成误导，``unused`` 表示读取后未用于决策，
            ``unknown`` 表示无法判断。
        reason: 模型给出的反馈理由；未提供时为空。
        host_kind: 实际运行端，当前为 ``cc`` 或 ``codex``；环境未提供或旧记录
            缺少该字段时为空。
        native_session_id: Claude Code 会话 ID 或 Codex thread ID；原生会话
            尚未生成或旧记录缺少该字段时为空。
    """

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
    """向记忆目录的访问日志追加一条记录。

    Args:
        root: 记忆根目录。
        rec: 要写入的搜索、命中或读取记录。
    """
    _append(Path(root) / _META_DIR / _ACCESS_LOG, asdict(rec))


def log_outcome(root: Path | str, rec: OutcomeRecord) -> None:
    """向记忆目录的反馈日志追加一条记录。

    Args:
        root: 记忆根目录。
        rec: 要写入的正文读取反馈。
    """
    _append(Path(root) / _META_DIR / _OUTCOME_LOG, asdict(rec))


def read_access_log(root: Path | str) -> list[AccessRecord]:
    """按写入顺序读取搜索、命中和正文读取记录。

    无法解析为 JSON 或无法构造成访问记录的行会告警并跳过，不影响其余记录。

    Args:
        root: 记忆根目录。

    Returns:
        成功解码的访问记录；日志不存在时为空列表。
    """
    return _read(Path(root) / _META_DIR / _ACCESS_LOG, AccessRecord)


def read_outcome_log(root: Path | str) -> list[OutcomeRecord]:
    """按写入顺序读取正文反馈记录。

    无法解析为 JSON 或无法构造成反馈记录的行会告警并跳过，不影响其余记录。

    Args:
        root: 记忆根目录。

    Returns:
        成功解码的反馈记录；日志不存在时为空列表。
    """
    return _read(Path(root) / _META_DIR / _OUTCOME_LOG, OutcomeRecord)


def _append(path: Path, obj: dict[str, Any]) -> None:
    """把一条记录编码为 JSON 并追加到日志文件。

    Args:
        path: 目标日志文件。
        obj: 要编码的记录字段。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _read(path: Path, cls: type[_RecordT]) -> list[_RecordT]:
    """读取能构造成指定记录类型的 JSON 行。

    无法解析为 JSON 或无法构造成 ``cls`` 的行会告警并跳过，不影响其余记录。

    Args:
        path: 要读取的日志文件。
        cls: 用于构造每条记录的类。

    Returns:
        与日志写入顺序一致的记录；文件不存在时为空列表。
    """
    if not path.exists():
        return []
    out: list[_RecordT] = []
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
