"""合并两种 runtime 的历史摘要，并提供 Trowel 自有分页游标。"""

from __future__ import annotations

import base64
import json
from typing import Any

from trowel_py.cc_host.session_scan import SessionSummary, list_sessions

_CURSOR_VERSION = 1


class HistoryCursorError(ValueError):
    """历史分页游标无法由当前版本解析。"""


def scan_cc_history(workdir: str, *, limit: int) -> list[SessionSummary]:
    return list_sessions(workdir, limit=limit)


def encode_history_cursor(offset: int) -> str:
    payload = json.dumps(
        {"offset": offset, "version": _CURSOR_VERSION},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_history_cursor(cursor: str) -> int:
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
    rows = [
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
