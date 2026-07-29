"""从 Tidy 报告中的有限失败信号判断水位能否前进。"""

from __future__ import annotations

from typing import Any


def _extract_failure(report: Any) -> str | None:
    """提取会阻止水位前进的首个失败信号。

    非字典报告视为失败；字典中只识别为真的顶层 ``skipped``，以及字典型
    ``tidy`` 中为真的 ``error``。先检查 ``skipped``，两者同时为真时也返回
    ``skipped``。其他缺失或未知字段不构成失败。

    Args:
        report: Tidy 调用返回值。

    Returns:
        分别以 ``non-dict report:``、``skipped:`` 或 ``error:`` 开头的失败
        文本；未识别到失败信号时为 ``None``。
    """
    if not isinstance(report, dict):
        return f"non-dict report: {type(report).__name__}"
    if report.get("skipped"):
        return f"skipped: {report['skipped']}"
    tidy = report.get("tidy")
    if isinstance(tidy, dict) and tidy.get("error"):
        return f"error: {tidy['error']}"
    return None


def tidy_succeeded(report: Any) -> bool:
    """判断 Tidy 报告是否允许水位前进。

    非字典报告、为真的顶层 ``skipped``，以及字典型 ``tidy`` 中为真的
    ``error`` 判为失败；其他字典均判为成功，包括空字典、未知字段和已知失败
    字段的假值。

    Args:
        report: Tidy 调用返回值。

    Returns:
        报告允许推进水位时为 ``True``。
    """
    return _extract_failure(report) is None
