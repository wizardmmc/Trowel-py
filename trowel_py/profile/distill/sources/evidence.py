"""从 Codex target journal 提取可被 Profile 门禁核验的用户原话。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from trowel_py.profile.distill.gate import DistillError
from trowel_py.profile.distill.models import EvidenceValidator
from trowel_py.profile.distill.sources.models import ProfileDistillSource

_DISCUSSION_SOURCE_ID = re.compile(
    r"^discussion:(?P<discussion_id>[A-Za-z0-9]+):message:(?P<message_id>[A-Za-z0-9]+)$"
)


def _event_user_text(event: dict[str, object]) -> str | None:
    """读取当前 normalized 事件或早期 native rollout 的用户正文。"""
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    if event.get("type") == "user":
        text = payload.get("text")
        return text if isinstance(text, str) and text.strip() else None
    if event.get("type") == "event_msg" and payload.get("type") == "user_message":
        message = payload.get("message")
        return message if isinstance(message, str) and message.strip() else None
    return None


def _read_target_user_texts(source: ProfileDistillSource) -> tuple[str, ...]:
    """读取全部 Codex target，并返回其中真实用户事件的正文。"""
    texts: list[str] = []
    for target in source.target:
        path = Path(target.path)
        found_user = False
        try:
            handle = path.open(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise DistillError(
                f"profile target journal unavailable for {source.source_id}: {path}"
            ) from exc
        try:
            with handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise DistillError(
                            "profile target journal contains invalid JSON for "
                            f"{source.source_id} at line {line_number}"
                        ) from exc
                    if not isinstance(event, dict):
                        raise DistillError(
                            "profile target journal contains a non-object event for "
                            f"{source.source_id} at line {line_number}"
                        )
                    text = _event_user_text(event)
                    if text is not None:
                        texts.append(text)
                        found_user = True
                        # 一个 Codex turn 只有一条用户起始事件；找到后不再扫描
                        # 可能很大的工具和模型输出。
                        break
        except (OSError, UnicodeDecodeError) as exc:
            raise DistillError(
                f"profile target journal unreadable for {source.source_id}: {path}"
            ) from exc
        if not found_user:
            raise DistillError(
                "profile target journal contains no user event for "
                f"{source.source_id}: {path}"
            )
    return tuple(texts)


def _read_discussion_user_texts(source: ProfileDistillSource) -> tuple[str, ...]:
    """严格读取 discussion target JSON，并拒绝参与者或错配来源身份。

    Args:
        source: runtime=discussion 且每个 target 为单条消息 JSON 的来源。

    Returns:
        按 target 顺序排列的用户原话。

    Raises:
        DistillError: 来源 ID、JSON schema、author 或消息身份不匹配。
    """

    identity = _DISCUSSION_SOURCE_ID.fullmatch(source.source_id)
    if identity is None or len(source.target) != 1:
        raise DistillError(f"invalid discussion profile source: {source.source_id}")
    target = source.target[0]
    try:
        payload = json.loads(Path(target.path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DistillError(
            f"discussion profile target unavailable for {source.source_id}"
        ) from exc
    if not isinstance(payload, dict):
        raise DistillError(
            f"discussion profile target is not an object for {source.source_id}"
        )
    valid_identity = (
        payload.get("schema") == "trowel.discussion.user-message.v1"
        and payload.get("author_role") == "user"
        and payload.get("discussion_id") == identity.group("discussion_id")
        and payload.get("message_id") == identity.group("message_id")
    )
    content = payload.get("content")
    if not valid_identity or not isinstance(content, str) or not content.strip():
        raise DistillError(
            f"discussion profile target identity mismatch for {source.source_id}"
        )
    return (content,)


def build_target_evidence_validator(
    source: ProfileDistillSource,
) -> EvidenceValidator | None:
    """为 Codex 来源创建只接受 target 用户原文片段的证据校验器。

    Claude Code 保持现有门禁行为，不在 Python 中重复解析其原生 transcript。
    """
    if source.runtime == "discussion":
        user_texts = _read_discussion_user_texts(source)
    elif source.runtime == "codex":
        user_texts = _read_target_user_texts(source)
    else:
        return None

    def validate(evidence: str) -> bool:
        """接受真实用户正文中出现的非空证据片段。"""
        cleaned = evidence.strip()
        return bool(cleaned) and any(cleaned in text for text in user_texts)

    return validate
