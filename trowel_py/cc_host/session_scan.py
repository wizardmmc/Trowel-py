"""列出指定工作目录下可恢复的 CC 历史会话。

扫描范围包括该目录中所有符合恢复列表过滤规则的 CC 主会话，不限于 Trowel
创建的记录。
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

# 标题扫描只读文件首尾以限制内存；64 KiB 头部用于越过首条消息前的大块元数据。
_HEAD_BYTES = 65536
_TAIL_BYTES = 8192

# 只检查 JSONL 首行的 sidechain 标记。
_SIDECHAIN_FIRST_LINE = re.compile(r'"isSidechain"\s*:\s*true')


@dataclass(frozen=True)
class SessionSummary:
    """保存一个可恢复 CC 会话在历史列表中展示的信息。

    Attributes:
        cc_session_id: 从 JSONL 文件名取得的原生 CC 会话 UUID。
        title: 按 customTitle、aiTitle、lastPrompt、头部首条非 tool_result 用户
            文本的顺序取得的非空标题。
        updated_at: JSONL 文件的修改时间，单位为 Unix 秒。
    """

    cc_session_id: str
    title: str
    updated_at: float


@dataclass(frozen=True)
class SessionConfigSummary:
    """保存 CC 历史会话最后确认的模型、思考强度和权限。

    Attributes:
        model: init 或 assistant 消息最后报告的非空模型；未报告时为 `None`。
        effort: attachment response 最后报告的非空思考强度；未报告时为 `None`。
        permission_mode: 顶层事件或 attachment response 最后报告的非空权限模式；
            未报告时为 `None`。
    """

    model: str | None
    effort: str | None
    permission_mode: str | None


def cc_projects_root(config_home: str | os.PathLike[str] | None = None) -> Path:
    """返回指定 Claude 用户家保存本地项目会话的根目录。

    Args:
        config_home: Claude 用户配置根；None 使用真实 `~/.claude` 兼容旧会话。
    """

    home = Path(config_home) if config_home is not None else Path.home() / ".claude"
    return home / "projects"


def workdir_to_slug(workdir: str | os.PathLike[str]) -> str:
    """将真实工作目录转换为 CC 使用的项目目录 slug。

    解析符号链接后，将每个非 ASCII 字母数字字符替换为连字符。
    当前未复现 CC 对超过 200 字符路径的哈希截断，超长路径可能无法命中。

    Args:
        workdir: 要映射到 CC projects 子目录的工作目录。

    Returns:
        CC projects 根目录下对应的子目录名。
    """
    return re.sub(r"[^a-zA-Z0-9]", "-", str(os.path.realpath(workdir)))


def _is_valid_uuid_session_id(stem: str) -> bool:
    """判断 JSONL 文件名主体是否是合法的 CC 会话 UUID。"""

    try:
        uuid.UUID(stem)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def count_sessions(
    workdir: str | os.PathLike[str],
    *,
    projects_root: Path | None = None,
) -> int:
    """按恢复列表的过滤规则统计工作目录中的可恢复 CC 会话。

    Args:
        workdir: 要统计历史会话的工作目录。

    Returns:
        排除 sidechain、非 UUID 文件和无标题 transcript 后的会话数。
    """
    return len(list_sessions(workdir, projects_root=projects_root))


def read_session_config(
    workdir: str | os.PathLike[str],
    cc_session_id: str,
    *,
    projects_root: Path | None = None,
) -> SessionConfigSummary | None:
    """顺序扫描主 transcript，提取最后报告的模型、思考强度和权限。

    无效会话 ID、文件缺失或不可读，以及没有任何已识别配置时返回 `None`。
    `<synthetic>` assistant 模型不作为实际模型。

    Args:
        workdir: CC 会话所属的工作目录。
        cc_session_id: 要读取的原生 CC 会话 UUID。
        projects_root: 会话所属 Claude 家的 projects 根；None 使用真实全局根。

    Returns:
        各字段最后一个非空配置值；无法取得任何配置时返回 `None`。
    """

    if not _is_valid_uuid_session_id(cc_session_id):
        return None
    path = (
        (projects_root or cc_projects_root())
        / workdir_to_slug(workdir)
        / f"{cc_session_id}.jsonl"
    )
    if not path.is_file():
        return None
    model: str | None = None
    effort: str | None = None
    permission_mode: str | None = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                event_type = event.get("type")
                candidate_model: object = None
                if event_type == "system" and event.get("subtype") == "init":
                    candidate_model = event.get("model")
                elif event_type == "assistant" and isinstance(
                    event.get("message"), dict
                ):
                    candidate_model = event["message"].get("model")
                if (
                    isinstance(candidate_model, str)
                    and candidate_model
                    and candidate_model != "<synthetic>"
                ):
                    model = candidate_model

                top_permission = event.get("permissionMode")
                if isinstance(top_permission, str) and top_permission:
                    permission_mode = top_permission

                attachment = event.get("attachment")
                response = (
                    attachment.get("response")
                    if isinstance(attachment, dict)
                    else None
                )
                if not isinstance(response, dict):
                    continue
                response_permission = response.get("permission_mode")
                if isinstance(response_permission, str) and response_permission:
                    permission_mode = response_permission
                response_effort = response.get("effort")
                level = (
                    response_effort.get("level")
                    if isinstance(response_effort, dict)
                    else None
                )
                if isinstance(level, str) and level:
                    effort = level
    except OSError:
        return None
    if model is None and effort is None and permission_mode is None:
        return None
    return SessionConfigSummary(
        model=model,
        effort=effort,
        permission_mode=permission_mode,
    )


def list_sessions(
    workdir: str | os.PathLike[str],
    *,
    limit: int | None = None,
    excluded_ids: frozenset[str] = frozenset(),
    projects_root: Path | None = None,
) -> list[SessionSummary]:
    """列出工作目录中按文件修改时间倒序排列的可恢复 CC 会话。

    只接收 UUID 命名且能提取非空标题的主 transcript。单个文件在扫描中消失、
    读取失败或标题提取抛出异常时跳过，不影响其他结果。

    Args:
        workdir: 要扫描历史会话的工作目录。
        limit: 修改时间倒序排序后使用的切片上限；`None` 返回全部，非负数返回前
            `limit` 条，负数按 Python 切片语义移除末尾相应条数。
        excluded_ids: 在标题读取、排序和切片前排除的 Claude Code session ID。
        projects_root: 要扫描的 Claude 家 projects 根；None 使用真实全局根。

    Returns:
        通过过滤的会话摘要；项目目录不存在时返回空列表。
    """
    slug = workdir_to_slug(workdir)
    proj_dir = (projects_root or cc_projects_root()) / slug
    if not proj_dir.is_dir():
        return []
    out: list[SessionSummary] = []
    for f in proj_dir.glob("*.jsonl"):
        if not _is_valid_uuid_session_id(f.stem):
            continue
        if f.stem in excluded_ids:
            continue
        try:
            mtime = f.stat().st_mtime
            title = _extract_title(f)
        except OSError:
            # CC 可能在扫描期间移动或删除文件，单个文件失败不影响列表。
            continue
        except Exception as exc:  # noqa: BLE001 — 单个坏文件不能阻断整个列表
            logger.debug("skipping unparseable session file %s: %s", f, exc)
            continue
        if title == "":
            # sidechain 和只含元数据的 transcript 都没有可展示标题。
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
    """以 UTF-8 容错解码并返回 JSONL 文件的有界首尾文本。

    Args:
        path: 要读取的 transcript 文件。

    Returns:
        最多 64 KiB 的头部和最多 8 KiB 的尾部；文件不超过头部上限时两者相同。
    """
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
    """按 customTitle、aiTitle、lastPrompt、首条真实用户文本的顺序选择标题。

    标题字段在各自的有界切片中取最后一个字符串值。首行为 sidechain 标记或
    无法提取标题时返回空字符串。

    Args:
        path: 要提取标题的主 transcript 文件。

    Returns:
        选中的标题；sidechain 或无可用标题时返回空字符串。
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
    """从可能不完整的 JSONL 文本中提取字段最后一个字符串值。

    Args:
        blob: transcript 的头部或尾部文本切片。
        field: 要查找的 JSON 字段名。

    Returns:
        解码 JSON 转义后的最后一个值；没有匹配时返回空字符串。值的转义无法
        解码时返回捕获到的原始字符串体。
    """
    pattern = re.compile(r'"' + re.escape(field) + r'"\s*:\s*"((?:[^"\\]|\\.)*)"')
    matches = pattern.findall(blob)
    if not matches:
        return ""
    raw = matches[-1]
    if not isinstance(raw, str):
        raise ValueError("title field pattern must contain exactly one capture group")
    try:
        # 正则只捕获 JSON 字符串体，这里补回引号以解码其中的转义。
        decoded = json.loads('"' + raw + '"')
        return decoded if isinstance(decoded, str) else raw
    except json.JSONDecodeError:
        return raw


def _first_user_text_from_head(head: str) -> str:
    """从头部切片提取首条真实用户文本，并忽略工具结果回显。

    Args:
        head: transcript 的头部文本切片。

    Returns:
        第一条可用用户文本；没有时返回空字符串。
    """
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
        # CC 把 tool_result 也封装为 user 事件，但它不是用户输入。
        if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        ):
            continue
        text = _extract_text(content)
        if text:
            return text
    return ""


def _extract_text(content: object) -> str:
    """从用户消息正文中取出字符串内容或第一个 text 块。

    Args:
        content: CC 用户消息的 `message.content` 值。

    Returns:
        直接字符串或第一个 text 块的文本；正文形态不支持时返回空字符串。
    """

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return str(block.get("text", ""))
    return ""
