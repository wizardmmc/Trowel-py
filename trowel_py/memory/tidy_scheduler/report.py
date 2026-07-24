"""Tidy 报告的成功判定。"""

from __future__ import annotations

from typing import Any


def _extract_failure(report: Any) -> str | None:
    """提取会阻止 watermark 前进的失败信号。"""
    if not isinstance(report, dict):
        return f"non-dict report: {type(report).__name__}"
    if report.get("skipped"):
        return f"skipped: {report['skipped']}"
    tidy = report.get("tidy")
    if isinstance(tidy, dict) and tidy.get("error"):
        return f"error: {tidy['error']}"
    return None


def tidy_succeeded(report: Any) -> bool:
    """无失败信号即成功；没有变更的干净报告也可推进 watermark。"""
    return _extract_failure(report) is None
