"""按整会话或片段路径保存和读取判效 JSON 报告。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from trowel_py.memory.judgements import (
    _JUDGEMENTS_DIR,
    _META_DIR,
    JudgementReport,
)
from trowel_py.memory.judgements.codec import _report_from_dict, _report_to_dict

logger = logging.getLogger("trowel_py.memory.judgements")


def _judgement_path(
    root: Path,
    cc_session_id: str,
    segment_id: str = "",
) -> Path:
    """根据会话和片段 ID 生成报告路径。

    空 ``segment_id`` 使用 ``meta/judgements/<session>.json``；非空值使用
    ``meta/judgements/<session>/<segment>.json``。函数只替换 ``segment_id``
    中的冒号；两个 ID 中的斜杠、``..`` 和绝对路径均不受限制，可能改变目录
    层级或跳出 ``meta/judgements``，调用方必须传入可信路径段。
    """
    if segment_id:
        safe = segment_id.replace(":", "_")
        return root / _META_DIR / _JUDGEMENTS_DIR / cc_session_id / f"{safe}.json"
    return root / _META_DIR / _JUDGEMENTS_DIR / f"{cc_session_id}.json"


def save_judgement_report(root: Path | str, report: JudgementReport) -> None:
    """把报告直接覆盖写入对应的 UTF-8 JSON 文件。

    父目录会按需创建；写入不使用临时文件或锁，也不主动校验报告字段，包括
    ``memory_id``。相同会话和片段的重跑会覆盖旧报告。
    """
    path = _judgement_path(Path(root), report.cc_session_id, report.segment_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_report_to_dict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_judgement_report(
    root: Path | str,
    cc_session_id: str,
) -> JudgementReport | None:
    """读取整会话平铺报告，不查找同会话的分段文件。

    Args:
        root: Memory 根目录。
        cc_session_id: 平铺文件名使用的 CC 会话 ID。

    Returns:
        解码后的报告；文件不存在时返回 ``None``。

    Raises:
        ValueError: 文件不是合法 JSON，或报告中的词表值不合法。
        OSError: 文件存在但无法读取。
        UnicodeError: 文件不是有效的 UTF-8。
        AttributeError: JSON 顶层不是对象。
        TypeError: 词表值不可哈希。
    """
    path = _judgement_path(Path(root), cc_session_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"corrupt judgement at {path}: {exc}") from exc
    return _report_from_dict(data)


def load_all_judgement_reports(root: Path | str) -> list[JudgementReport]:
    """递归读取全部报告，并避免整会话与分段结果双计数。

    文件先按路径排序，再按报告正文中的 ``cc_session_id`` 分组。同一会话只要
    存在非空 ``segment_id``，就只返回这些分段报告并忽略平铺报告；否则返回
    该组全部报告。JSON 损坏、词表非法或发生 ``OSError`` 的文件会告警并
    跳过。JSON 顶层不是对象、词表值不可哈希或文件不是有效 UTF-8 等未捕获
    错误仍会中止加载。

    Args:
        root: Memory 根目录。

    Returns:
        按会话首次出现顺序分组、组内保持排序路径顺序的有效报告；目录不存在时
        返回空列表。
    """
    directory = Path(root) / _META_DIR / _JUDGEMENTS_DIR
    if not directory.exists():
        return []
    by_session: dict[str, list[JudgementReport]] = {}
    for path in sorted(directory.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            report = _report_from_dict(data)
        except (ValueError, json.JSONDecodeError, OSError):
            logger.warning("skipping corrupt judgement file: %s", path.name)
            continue
        by_session.setdefault(report.cc_session_id, []).append(report)

    result: list[JudgementReport] = []
    for session_id, reports in by_session.items():
        segmented = [report for report in reports if report.segment_id]
        if not segmented:
            result.extend(reports)
            continue
        result.extend(segmented)
        if any(not report.segment_id for report in reports):
            logger.info(
                "legacy flat judgement for %s ignored (segment-level present)",
                session_id,
            )
    return result
