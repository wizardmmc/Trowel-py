"""读写独立于 Daily review 的 Profile 提炼处理进度。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_META_DIR = "meta"
_STATE_FILE = "profile-distill-state.json"


@dataclass(frozen=True)
class ProcessedSession:
    """记录一个 CC 会话已完成 Profile 提炼的位置。

    字段只用于持久化和增量范围计算，不在构造时校验格式或取值范围。

    Attributes:
        cc_session_id: 水位所属的 CC 会话 ID。
        end_offset: 已完成提炼的 JSONL 结束字节偏移。
        at: 写入该水位时的时间文本。
    """

    cc_session_id: str
    end_offset: int
    at: str


@dataclass(frozen=True)
class ProcessedCodexTurn:
    """记录一个 Codex turn 已完成 Profile 提炼。

    Attributes:
        thread_id: Codex 原生 thread ID。
        turn_id: thread 内唯一的原生 turn ID。
        at: 成功通过门禁并写入建议队列后的记录时间。
    """

    thread_id: str
    turn_id: str
    at: str


def _state_path(root: Path) -> Path:
    """定位 Profile 提炼的独立水位文件。

    Args:
        root: Memory 根目录。

    Returns:
        ``<root>/meta/profile-distill-state.json``。
    """
    return root / _META_DIR / _STATE_FILE


def load_processed(root: Path) -> dict[str, ProcessedSession]:
    """读取 Profile 提炼水位并按会话 ID 去重。

    文件缺失或 JSON 顶层不是对象时返回空映射。``processed`` 条目缺少会话
    ID 或不是对象时静默跳过；``end_offset`` 无法转为整数时记录告警并跳过。
    缺失 offset 取 0，其他值直接交给 ``int()``，因此布尔值、可解析的整数
    字符串和有限浮点数也会被接受，有限浮点数向零截断；会话 ID 和时间则
    直接转为字符串。函数不校验空 ID、负 offset 或时间格式，重复 ID 由
    最后一条成功转换的记录覆盖。

    Args:
        root: Memory 根目录。

    Returns:
        以会话 ID 为键的成功转换的水位；保持各 ID 首次插入的顺序。

    Raises:
        OSError: 无法读取水位文件。
        UnicodeDecodeError: 水位文件不是有效的 UTF-8 文本。
        ValueError: 水位文件不是合法 JSON。
        TypeError: ``processed`` 存在但不是可迭代值。
        OverflowError: ``end_offset`` 是无法转换为整数的非有限浮点数。
    """
    path = _state_path(root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"corrupt distill state at {path}: {exc}") from exc
    raw = data.get("processed", []) if isinstance(data, dict) else []
    out: dict[str, ProcessedSession] = {}
    for item in raw:
        if not isinstance(item, dict) or "cc_session_id" not in item:
            continue
        # 无法转换的 offset 不阻塞整批加载；没有其他可用记录时不保留该 ID。
        try:
            end_offset = int(item.get("end_offset", 0))
        except (TypeError, ValueError):
            logger.warning(
                "distill state: corrupt end_offset %r for %s, skipping",
                item.get("end_offset"),
                item.get("cc_session_id"),
            )
            continue
        rec = ProcessedSession(
            cc_session_id=str(item["cc_session_id"]),
            end_offset=end_offset,
            at=str(item.get("at", "")),
        )
        out[rec.cc_session_id] = rec
    return out


def load_codex_processed(
    root: Path,
) -> dict[tuple[str, str], ProcessedCodexTurn]:
    """读取 Profile 已处理的 Codex turn 记录。

    旧版只有 ``processed`` Claude 字节水位时返回空映射。条目不是对象或缺少
    非空 thread/turn ID 时跳过；重复身份由最后一条记录覆盖。

    Args:
        root: Memory 根目录。

    Returns:
        以 ``(thread_id, turn_id)`` 为键的 Codex 处理记录。

    Raises:
        OSError: 无法读取水位文件。
        UnicodeDecodeError: 水位文件不是有效 UTF-8。
        ValueError: 水位文件不是合法 JSON。
        TypeError: ``codex_processed`` 存在但不可迭代。
    """
    path = _state_path(root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"corrupt distill state at {path}: {exc}") from exc
    raw = data.get("codex_processed", []) if isinstance(data, dict) else []
    out: dict[tuple[str, str], ProcessedCodexTurn] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        thread_id = str(item.get("thread_id", "")).strip()
        turn_id = str(item.get("turn_id", "")).strip()
        if not thread_id or not turn_id:
            continue
        record = ProcessedCodexTurn(
            thread_id=thread_id,
            turn_id=turn_id,
            at=str(item.get("at", "")),
        )
        out[(thread_id, turn_id)] = record
    return out


def _write_state(
    root: Path,
    claude: dict[str, ProcessedSession],
    codex: dict[tuple[str, str], ProcessedCodexTurn],
) -> None:
    """覆盖写入 Claude 字节水位和 Codex turn 处理记录。"""
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "processed": [
            {
                "cc_session_id": record.cc_session_id,
                "end_offset": record.end_offset,
                "at": record.at,
            }
            for record in claude.values()
        ],
        "codex_processed": [
            {
                "thread_id": record.thread_id,
                "turn_id": record.turn_id,
                "at": record.at,
            }
            for record in codex.values()
        ],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def mark_processed(root: Path, cc_session_id: str, end_offset: int, *, at: str) -> None:
    """读取可加载的水位、替换指定会话，并覆盖写回状态文件。

    新会话追加在现有顺序末尾，已有会话只替换值而不改变位置。更新采用非原子
    的 read-modify-write，调用方必须持有 Profile 提炼进程锁；写入中断可能
    留下不完整 JSON。写回只保留成功加载且按 ID 去重后的旧记录，原文件中被
    跳过的畸形条目和较早的重复条目会丢失。

    Args:
        root: Memory 根目录。
        cc_session_id: 要新增或替换的会话 ID。
        end_offset: 已完成提炼的 JSONL 结束字节偏移。
        at: 本次写入使用的时间文本。

    Raises:
        OSError: 无法读取旧水位、创建 ``meta`` 目录或写入状态文件。
        UnicodeDecodeError: 旧水位不是有效的 UTF-8 文本。
        ValueError: 旧水位不是合法 JSON。
        TypeError: 旧 ``processed`` 不可迭代，或字段无法序列化为 JSON。
        OverflowError: 旧水位含无法转换为整数的非有限浮点数。
    """
    existing = load_processed(root)
    codex = load_codex_processed(root)
    existing[cc_session_id] = ProcessedSession(
        cc_session_id=cc_session_id, end_offset=end_offset, at=at
    )
    _write_state(root, existing, codex)


def mark_codex_processed(
    root: Path,
    thread_id: str,
    turn_id: str,
    *,
    at: str,
) -> None:
    """记录一个 Codex turn 已完成 Profile 提炼并保留 Claude 水位。

    调用方必须先成功持久化本 turn 生成的建议，再调用本函数。更新采用
    read-modify-write，调用方必须持有 Profile 提炼进程锁。

    Args:
        root: Memory 根目录。
        thread_id: Codex 原生 thread ID。
        turn_id: thread 内唯一的原生 turn ID。
        at: 本次写入使用的时间文本。
    """
    if not thread_id or not turn_id:
        raise ValueError("Codex Profile watermark requires thread and turn ids")
    claude = load_processed(root)
    existing = load_codex_processed(root)
    existing[(thread_id, turn_id)] = ProcessedCodexTurn(
        thread_id=thread_id,
        turn_id=turn_id,
        at=at,
    )
    _write_state(root, claude, existing)
