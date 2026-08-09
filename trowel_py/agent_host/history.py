"""扫描 Claude Code 历史会话，并对 Claude Code 与 Codex 的历史记录进行合并、排序和分页。"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from trowel_py.cc_host.session_scan import SessionSummary, list_sessions

_CURSOR_VERSION = 1


class HistoryCursorError(ValueError):
    """表示历史分页游标格式错误或版本不受支持。"""


def scan_cc_history(
    workdir: str,
    *,
    limit: int,
    excluded_ids: frozenset[str] = frozenset(),
    projects_roots: Sequence[Path] | None = None,
) -> list[SessionSummary]:
    """读取最近的 Claude Code 历史，并在截断前排除指定原生会话。

    Args:
        workdir: 要扫描历史会话的工作目录。
        limit: 最多返回的非排除会话数。
        excluded_ids: 已确认属于 Trowel 委派子会话的 Claude Code session ID。
        projects_roots: 需要合并的 Claude projects 根。None 使用
            真实全局 Claude 根，供旧调用方兼容。

    Returns:
        按更新时间倒序排列且已按原生会话 ID 去重的摘要。
    """

    if projects_roots is None:
        return list_sessions(workdir, limit=limit, excluded_ids=excluded_ids)
    newest_by_id: dict[str, SessionSummary] = {}
    for projects_root in projects_roots:
        for summary in list_sessions(
            workdir,
            limit=limit,
            excluded_ids=excluded_ids,
            projects_root=projects_root,
        ):
            current = newest_by_id.get(summary.cc_session_id)
            if current is None or summary.updated_at > current.updated_at:
                newest_by_id[summary.cc_session_id] = summary
    return sorted(
        newest_by_id.values(),
        key=lambda summary: (-summary.updated_at, summary.cc_session_id),
    )[:limit]


def encode_history_cursor(offset: int) -> str:
    """将历史记录的分页位置编码为可放入 URL 的游标。

    Args:
        offset: 下一页从第几条历史记录开始读取，0 表示第一条。

    Returns:
        包含格式版本和分页位置的游标文本。
    """

    payload = json.dumps(
        {"offset": offset, "version": _CURSOR_VERSION},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_history_cursor(cursor: str) -> int:
    """解析历史分页游标，取得下一页的起始位置。

    Args:
        cursor: encode_history_cursor 生成的游标文本。

    Returns:
        下一页从第几条历史记录开始读取。

    Raises:
        HistoryCursorError: 游标格式错误、版本不受支持或分页位置无效。
    """

    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(
            cursor + padding,
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoryCursorError("invalid history cursor") from exc
    if not isinstance(payload, dict) or payload.get("version") != _CURSOR_VERSION:
        raise HistoryCursorError("invalid history cursor")
    offset = payload.get("offset")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise HistoryCursorError("invalid history cursor")
    return offset


def merge_history_page(
    cc_summaries: list[SessionSummary],
    codex_threads: list[dict[str, Any]],
    *,
    offset: int,
    limit: int,
) -> tuple[list[dict[str, Any]], str | None]:
    """合并 Claude Code 会话摘要和 Codex thread，并按更新时间返回一页。

    缺少有效 ID 或更新时间的 Codex thread 会被忽略。结果按更新时间从新到旧排列；
    更新时间相同时，再按运行工具和会话 ID 排序。

    Args:
        cc_summaries: Claude Code 历史会话列表，每项包含会话 ID、标题和更新时间。
        codex_threads: Codex 返回的 thread 记录。
        offset: 本页从合并结果中的第几条记录开始。
        limit: 本页最多返回的记录数。

    Returns:
        历史记录列表和下一页游标。每条记录包含 runtime、native_session_id、title
        和 updated_at；没有下一页时游标为 None。
    """

    rows: list[dict[str, Any]] = [
        {
            "runtime": "claude_code",
            "native_session_id": summary.cc_session_id,
            "title": summary.title,
            "updated_at": summary.updated_at,
        }
        for summary in cc_summaries
    ]
    for thread in codex_threads:
        thread_id = thread.get("id")
        updated_at = thread.get("updatedAt")
        if not isinstance(thread_id, str) or not isinstance(updated_at, (int, float)):
            continue
        title = thread.get("name") or thread.get("preview") or "(无标题)"
        rows.append(
            {
                "runtime": "codex",
                "native_session_id": thread_id,
                "title": str(title),
                "updated_at": updated_at,
            }
        )
    rows.sort(
        key=lambda row: (
            -float(row["updated_at"]),
            str(row["runtime"]),
            str(row["native_session_id"]),
        )
    )
    page = rows[offset : offset + limit]
    next_offset = offset + len(page)
    next_cursor = (
        encode_history_cursor(next_offset) if next_offset < len(rows) else None
    )
    return page, next_cursor
