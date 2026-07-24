"""judgement 报告的文件仓储。"""

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
    if segment_id:
        safe = segment_id.replace(":", "_")
        return root / _META_DIR / _JUDGEMENTS_DIR / cc_session_id / f"{safe}.json"
    return root / _META_DIR / _JUDGEMENTS_DIR / f"{cc_session_id}.json"


def save_judgement_report(root: Path | str, report: JudgementReport) -> None:
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
    """读取 legacy session 报告；缺失返回 None，损坏则显式报错。"""
    path = _judgement_path(Path(root), cc_session_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"corrupt judgement at {path}: {exc}") from exc
    return _report_from_dict(data)


def load_all_judgement_reports(root: Path | str) -> list[JudgementReport]:
    """读取全部有效报告，并避免 legacy 与 segment 双计数。"""
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
