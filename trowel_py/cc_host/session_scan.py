"""列出指定工作目录下可恢复的 CC 历史会话。

扫描范围包括该目录的全部 CC 会话，不限于 Trowel 创建的记录。
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# 只读文件首尾以限制内存；头部需覆盖首条用户消息前的大块元数据。
_HEAD_BYTES = 65536
_TAIL_BYTES = 8192

# sidechain 标记只在首行生效。
_SIDECHAIN_FIRST_LINE = re.compile(r'"isSidechain"\s*:\s*true')


@dataclass(frozen=True)
class SessionSummary:
    cc_session_id: str
    title: str
    updated_at: float  # 文件修改时间，Unix 秒


def cc_projects_root() -> Path:
    return Path.home() / ".claude" / "projects"


def workdir_to_slug(workdir: str | os.PathLike) -> str:
    """将真实工作目录转换为 CC 使用的项目目录 slug。

    解析符号链接后，将每个非 ASCII 字母数字字符替换为连字符。
    当前未复现 CC 对超过 200 字符路径的哈希截断，超长路径可能无法命中。
    """
    return re.sub(r"[^a-zA-Z0-9]", "-", str(os.path.realpath(workdir)))


def _is_valid_uuid_session_id(stem: str) -> bool:
    try:
        uuid.UUID(stem)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def count_sessions(workdir: str | os.PathLike) -> int:
    """复用恢复列表的过滤规则，避免裸 glob 计入 sidechain 和元数据文件。"""
    return len(list_sessions(workdir))


def list_sessions(
    workdir: str | os.PathLike, *, limit: int | None = None
) -> list[SessionSummary]:
    """返回按更新时间倒序排列的可恢复会话，可限制数量。"""
    slug = workdir_to_slug(workdir)
    proj_dir = cc_projects_root() / slug
    if not proj_dir.is_dir():
        return []
    out: list[SessionSummary] = []
    for f in proj_dir.glob("*.jsonl"):
        if not _is_valid_uuid_session_id(f.stem):
            continue
        try:
            mtime = f.stat().st_mtime
            title = _extract_title(f)
        except OSError:
            # CC 可能在扫描期间轮转文件。
            continue
        except Exception as exc:  # noqa: BLE001 — 单个坏文件不能阻断整个列表
            logger.debug("skipping unparseable session file %s: %s", f, exc)
            continue
        if title == "":
            # 无可展示标题的文件不加入恢复列表。
            continue
        out.append(
            SessionSummary(
                cc_session_id=f.stem,
                title=title,
                updated_at=mtime,
            )
        )
    out.sort(key=lambda s: s.updated_at, reverse=True)
    if limit is not None:
        return out[:limit]
    return out


def _read_head_tail(path: Path) -> tuple[str, str]:
    """以 UTF-8 容错解码并返回文件的有界首尾文本。"""
    size = path.stat().st_size
    head_text = ""
    tail_text = ""
    with path.open("rb") as fh:
        if size > 0:
            head_bytes = min(_HEAD_BYTES, size)
            fh.seek(0)
            head_text = fh.read(head_bytes).decode("utf-8", errors="replace")
        if size > _HEAD_BYTES:
            fh.seek(size - _TAIL_BYTES)
            tail_text = fh.read().decode("utf-8", errors="replace")
        elif size > 0:
            tail_text = head_text
    return head_text, tail_text


def _extract_title(path: Path) -> str:
    """按 customTitle、aiTitle、lastPrompt、首条用户文本选择标题。

    首行为 sidechain 标记或无法提取标题时返回空字符串。
    """
    head, tail = _read_head_tail(path)

    first_line = head.split("\n", 1)[0]
    if _SIDECHAIN_FIRST_LINE.search(first_line):
        return ""

    custom = _last_string_field(tail, "customTitle") or _last_string_field(head, "customTitle")
    if custom:
        return custom
    ai = _last_string_field(tail, "aiTitle") or _last_string_field(head, "aiTitle")
    if ai:
        return ai
    last_prompt = _last_string_field(tail, "lastPrompt")
    if last_prompt:
        return last_prompt
    return _first_user_text_from_head(head)


def _last_string_field(blob: str, field: str) -> str:
    """从不完整 JSONL 文本中提取字段最后一个字符串值。"""
    pattern = re.compile(r'"' + re.escape(field) + r'"\s*:\s*"((?:[^"\\]|\\.)*)"')
    matches = pattern.findall(blob)
    if not matches:
        return ""
    raw = matches[-1]
    try:
        # 捕获的是 JSON 字符串体，需要再次解码转义。
        return json.loads('"' + raw + '"')
    except json.JSONDecodeError:
        return raw


def _first_user_text_from_head(head: str) -> str:
    """从头部切片提取首条真实用户文本，忽略工具结果回显。"""
    for raw in head.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") != "user":
            continue
        content = ev.get("message", {}).get("content")
        # tool_result 也封装为 user 事件，不能作为会话标题。
        if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        ):
            continue
        text = _extract_text(content)
        if text:
            return text
    return ""


def _extract_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return str(block.get("text", ""))
    return ""
