"""显式 Memory URI 的安全读取、脱敏与近期窗口校验。"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone

from trowel_py.memory.store import MemoryStore

from .models import DefaultWorkError, SampledSource

SOURCE_CHAR_CAP = 900
SOURCE_COUNT_CAP = 3

_ABSOLUTE_PATH = re.compile(r"(?:/Users|/home|/var|/tmp)/[^\s`\"']+")
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.I,
)
_URL = re.compile(r"https?://[^\s)>\]\"']+")
_SECRET = re.compile(
    r"(?i)(api[_-]?key|authorization|password|secret|token)\s*[:=]\s*"
    r"(?:bearer\s+)?\S+"
)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _redact(value: str) -> str:
    value = _SECRET.sub(r"\1=<redacted>", value)
    value = _EMAIL.sub("<email>", value)
    value = _UUID.sub("<session-id>", value)
    value = _ABSOLUTE_PATH.sub("<path>", value)
    return _URL.sub("<url>", value)


def _stem(uri: str) -> str:
    prefix = "memory://notes/"
    if not uri.startswith(prefix):
        raise DefaultWorkError("source_missing")
    value = uri[len(prefix) :]
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise DefaultWorkError("source_missing")
    return value


def _command_date(occurred_at: datetime) -> date:
    if occurred_at.tzinfo is None:
        raise ValueError("occurred_at must be timezone-aware")
    return occurred_at.astimezone(timezone.utc).date()


def sample_sources(
    store: MemoryStore,
    source_refs: tuple[str, ...],
    *,
    occurred_at: datetime,
) -> tuple[SampledSource, ...]:
    """按命令顺序读取 1..3 条 active note，不执行任何自动检索。"""

    if not 1 <= len(source_refs) <= SOURCE_COUNT_CAP:
        raise DefaultWorkError("invalid_source_count")
    if len(set(source_refs)) != len(source_refs):
        raise DefaultWorkError("invalid_source_count", "source_refs must be unique")
    today = _command_date(occurred_at)
    allowed_dates = {today, today - timedelta(days=1)}
    sampled: list[SampledSource] = []
    for uri in source_refs:
        stem = _stem(uri)
        declared_notes_root = store.root / "notes"
        if declared_notes_root.is_symlink():
            raise DefaultWorkError("source_missing")
        notes_root = declared_notes_root.resolve()
        note_path = declared_notes_root / f"{stem}.md"
        try:
            resolved = note_path.resolve(strict=True)
        except OSError as exc:
            raise DefaultWorkError("source_missing") from exc
        if note_path.is_symlink() or resolved.parent != notes_root:
            raise DefaultWorkError("source_missing")
        note = store.load_note(stem)
        if note is None:
            raise DefaultWorkError("source_missing")
        if not note.memory_id.strip():
            raise DefaultWorkError("source_missing")
        if note.status != "active":
            raise DefaultWorkError("source_inactive")
        if not _DATE.fullmatch(note.updated):
            raise DefaultWorkError("source_not_recent")
        try:
            updated = date.fromisoformat(note.updated)
        except ValueError as exc:
            raise DefaultWorkError("source_not_recent") from exc
        if updated not in allowed_dates:
            raise DefaultWorkError("source_not_recent")
        text = _redact(
            "\n".join(
                part.strip()
                for part in (
                    f"标题：{note.title}",
                    f"摘要：{note.summary}",
                    note.body,
                )
                if part.strip()
            )
        )[:SOURCE_CHAR_CAP]
        sampled.append(
            SampledSource(
                uri_at_generation=uri,
                memory_id=note.memory_id,
                sampled_content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                updated=note.updated,
                chars=len(text),
                text=text,
            )
        )
    return tuple(sampled)
