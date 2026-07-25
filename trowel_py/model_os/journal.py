"""Model OS journal 的身份、结构校验与公开读取值对象。"""

from __future__ import annotations

import hashlib
import json
import math
import re
import base64
import sqlite3
from datetime import datetime
from typing import Any, Callable
from dataclasses import dataclass

from trowel_py.model_os.redaction import redact_payload
from trowel_py.model_os.types import DecisionDisposition, DecisionRecord, EventEnvelope


_REASON_CODE = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_STRUCTURED_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
_HASH_TOKEN = re.compile(r"sha256:[A-Za-z0-9_-]{6,128}\Z")


class JournalIdentityConflict(ValueError):
    """同一 journal ID 被用于不同语义内容。"""

    def __init__(self, entry_type: str, entry_id: str) -> None:
        self.entry_type = entry_type
        self.entry_id = entry_id
        super().__init__("journal identity conflict")


class InvalidJournalCursor(ValueError):
    """cursor 损坏、版本错误或与查询过滤器不匹配。"""

    def __init__(self) -> None:
        super().__init__("invalid journal cursor")


@dataclass(frozen=True)
class JournalBoundary:
    event_seq: int = 0
    decision_seq: int = 0


@dataclass(frozen=True)
class JournalFilter:
    kinds: tuple[str, ...] | None = None
    task_id: str | None = None
    episode_id: str | None = None
    correlation_id: str | None = None


@dataclass(frozen=True)
class JournalCursor:
    filter_hash: str
    as_of: JournalBoundary
    recorded_at: str
    stream: str
    stream_seq: int


@dataclass(frozen=True)
class JournalEntrySummary:
    stream: str
    stream_seq: int
    entry_id: str
    kind: str
    recorded_at: str
    work_item_id: str | None
    task_id: str | None
    episode_id: str | None
    cause_id: str | None
    correlation_id: str | None
    policy_version: str
    provenance: str | None = None
    outcome: str | None = None


@dataclass(frozen=True)
class JournalPage:
    items: tuple[JournalEntrySummary, ...]
    as_of: JournalBoundary
    next_cursor: str | None


_CURSOR_VERSION = 1


def journal_filter_hash(journal_filter: JournalFilter) -> str:
    body = {
        "kinds": sorted(journal_filter.kinds) if journal_filter.kinds is not None else None,
        "task_id": journal_filter.task_id,
        "episode_id": journal_filter.episode_id,
        "correlation_id": journal_filter.correlation_id,
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _cursor_checksum(body: dict[str, Any]) -> str:
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def encode_journal_cursor(cursor: JournalCursor) -> str:
    body: dict[str, Any] = {
        "v": _CURSOR_VERSION,
        "filter_hash": cursor.filter_hash,
        "as_of": {
            "event_seq": cursor.as_of.event_seq,
            "decision_seq": cursor.as_of.decision_seq,
        },
        "after": {
            "recorded_at": cursor.recorded_at,
            "stream": cursor.stream,
            "stream_seq": cursor.stream_seq,
        },
    }
    body["checksum"] = _cursor_checksum(body)
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_journal_cursor(raw_cursor: str, expected_filter_hash: str) -> JournalCursor:
    try:
        padded = raw_cursor + "=" * (-len(raw_cursor) % 4)
        body = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
        if not isinstance(body, dict):
            raise ValueError
        checksum = body.pop("checksum")
        if not isinstance(checksum, str) or checksum != _cursor_checksum(body):
            raise ValueError
        if body.get("v") != _CURSOR_VERSION:
            raise ValueError
        if body.get("filter_hash") != expected_filter_hash:
            raise ValueError
        as_of = body["as_of"]
        after = body["after"]
        stream = after["stream"]
        if stream not in {"decision", "event"}:
            raise ValueError
        boundary = JournalBoundary(
            event_seq=int(as_of["event_seq"]),
            decision_seq=int(as_of["decision_seq"]),
        )
        cursor = JournalCursor(
            filter_hash=expected_filter_hash,
            as_of=boundary,
            recorded_at=str(after["recorded_at"]),
            stream=stream,
            stream_seq=int(after["stream_seq"]),
        )
        if min(boundary.event_seq, boundary.decision_seq, cursor.stream_seq) < 0:
            raise ValueError
        return cursor
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InvalidJournalCursor() from exc


def capture_journal_boundary(conn: sqlite3.Connection) -> JournalBoundary:
    event_row = conn.execute("SELECT COALESCE(MAX(seq), 0) AS seq FROM events").fetchone()
    decision_row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) AS seq FROM decisions"
    ).fetchone()
    return JournalBoundary(
        event_seq=int(event_row["seq"]),
        decision_seq=int(decision_row["seq"]),
    )


def _filter_sql(journal_filter: JournalFilter, params: list[Any]) -> str:
    clauses: list[str] = []
    if journal_filter.kinds is not None:
        kinds = tuple(sorted(set(journal_filter.kinds)))
        if not kinds:
            clauses.append("0")
        else:
            clauses.append(f"kind IN ({','.join('?' for _ in kinds)})")
            params.extend(kinds)
    for column, value in (
        ("task_id", journal_filter.task_id),
        ("episode_id", journal_filter.episode_id),
        ("correlation_id", journal_filter.correlation_id),
    ):
        if value is not None:
            clauses.append(f"{column}=?")
            params.append(value)
    return "".join(f" AND {clause}" for clause in clauses)


def _read_summary_stream(
    conn: sqlite3.Connection,
    *,
    stream: str,
    through_seq: int,
    after: JournalCursor | None,
    journal_filter: JournalFilter,
    limit: int,
) -> list[JournalEntrySummary]:
    if stream == "decision":
        table = "decisions"
        time_column = "decided_at"
        id_column = "decision_id"
        rank = 0
        extra_columns = "NULL AS provenance, NULL AS outcome"
    else:
        table = "events"
        time_column = "occurred_at"
        id_column = "event_id"
        rank = 1
        extra_columns = "provenance, outcome"
    params: list[Any] = [through_seq]
    filter_sql = _filter_sql(journal_filter, params)
    after_sql = ""
    if after is not None:
        after_rank = 0 if after.stream == "decision" else 1
        after_sql = (
            f" AND ({time_column} > ? OR "
            f"({time_column} = ? AND (? > ? OR (? = ? AND seq > ?))))"
        )
        params.extend(
            [
                after.recorded_at,
                after.recorded_at,
                rank,
                after_rank,
                rank,
                after_rank,
                after.stream_seq,
            ]
        )
    params.append(limit)
    rows = conn.execute(
        f"SELECT seq, {id_column} AS entry_id, kind, {time_column} AS recorded_at, "
        "work_item_id, task_id, episode_id, cause_id, correlation_id, policy_version, "
        f"{extra_columns} FROM {table} WHERE seq <= ?{filter_sql}{after_sql} "
        f"ORDER BY {time_column}, seq LIMIT ?",
        params,
    ).fetchall()
    if any(not _is_safe_recorded_at(row["recorded_at"]) for row in rows):
        raise ValueError("journal contains an invalid recorded_at value")
    return [
        JournalEntrySummary(
            stream=stream,
            stream_seq=int(row["seq"]),
            entry_id=row["entry_id"],
            kind=row["kind"],
            recorded_at=row["recorded_at"],
            work_item_id=row["work_item_id"],
            task_id=row["task_id"],
            episode_id=row["episode_id"],
            cause_id=row["cause_id"],
            correlation_id=row["correlation_id"],
            policy_version=row["policy_version"],
            provenance=row["provenance"],
            outcome=row["outcome"],
        )
        for row in rows
    ]


def read_journal_page(
    conn: sqlite3.Connection,
    *,
    journal_filter: JournalFilter,
    limit: int,
    cursor: str | None,
) -> JournalPage:
    if limit < 1 or limit > 500:
        raise ValueError("limit must be between 1 and 500")
    filter_hash = journal_filter_hash(journal_filter)
    decoded = (
        decode_journal_cursor(cursor, filter_hash) if cursor is not None else None
    )
    boundary = decoded.as_of if decoded is not None else capture_journal_boundary(conn)
    decisions = _read_summary_stream(
        conn,
        stream="decision",
        through_seq=boundary.decision_seq,
        after=decoded,
        journal_filter=journal_filter,
        limit=limit + 1,
    )
    events = _read_summary_stream(
        conn,
        stream="event",
        through_seq=boundary.event_seq,
        after=decoded,
        journal_filter=journal_filter,
        limit=limit + 1,
    )
    merged = sorted(
        decisions + events,
        key=lambda item: (
            item.recorded_at,
            0 if item.stream == "decision" else 1,
            item.stream_seq,
        ),
    )
    items = tuple(merged[:limit])
    next_cursor = None
    if len(merged) > limit and items:
        last = items[-1]
        next_cursor = encode_journal_cursor(
            JournalCursor(
                filter_hash=filter_hash,
                as_of=boundary,
                recorded_at=last.recorded_at,
                stream=last.stream,
                stream_seq=last.stream_seq,
            )
        )
    return JournalPage(items=items, as_of=boundary, next_cursor=next_cursor)


def _disposition_value(disposition: DecisionDisposition | str) -> str:
    if isinstance(disposition, DecisionDisposition):
        return disposition.value
    try:
        return DecisionDisposition(disposition).value
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid decision disposition") from exc


def _is_structured_token(value: Any) -> bool:
    return isinstance(value, str) and _STRUCTURED_TOKEN.fullmatch(value) is not None


def _is_safe_recorded_at(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > 64:
        return False
    if value.isdecimal():
        return True
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def validate_event_metadata(event: EventEnvelope) -> None:
    if not _is_safe_recorded_at(event.occurred_at):
        raise ValueError("event occurred_at must be an absolute timestamp")


def _validate_scalar(value: Any, *, field: str) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    if _is_structured_token(value):
        return
    raise ValueError(f"{field} must contain only structured JSON scalars")


def _validate_structured_json(value: Any, *, field: str, depth: int = 0) -> None:
    if depth > 4:
        raise ValueError(f"{field} nesting is too deep")
    if isinstance(value, dict):
        if len(value) > 64:
            raise ValueError(f"{field} contains too many fields")
        for key, item in value.items():
            if not _is_structured_token(key):
                raise ValueError(f"{field} contains an invalid key")
            _validate_structured_json(item, field=field, depth=depth + 1)
        return
    if isinstance(value, list):
        if len(value) > 64:
            raise ValueError(f"{field} contains too many items")
        for item in value:
            _validate_structured_json(item, field=field, depth=depth + 1)
        return
    _validate_scalar(value, field=field)


def validate_decision(decision: DecisionRecord, *, allow_legacy: bool = False) -> None:
    disposition = DecisionDisposition(_disposition_value(decision.disposition))
    if disposition == DecisionDisposition.LEGACY_UNKNOWN and not allow_legacy:
        raise ValueError("legacy_unknown is read-only")
    if not _is_safe_recorded_at(decision.decided_at):
        raise ValueError("decision decided_at must be an absolute timestamp")
    if disposition != DecisionDisposition.LEGACY_UNKNOWN:
        structured_fields = {
            "signals": decision.signals,
            "candidates": decision.candidates,
            "choice": decision.choice,
            "reason": decision.reason,
            "budget_before": decision.budget_before,
            "budget_after": decision.budget_after,
        }
        if redact_payload(structured_fields) != structured_fields:
            raise ValueError("decision contains a secret-shaped value")
        if _REASON_CODE.fullmatch(decision.reason) is None:
            raise ValueError("reason must be a stable reason_code")
        if set(decision.signals) != {"refs"}:
            raise ValueError("signals must contain only refs")
        refs = decision.signals.get("refs")
        if not isinstance(refs, list) or not all(_is_structured_token(v) for v in refs):
            raise ValueError("signals.refs must be a list of event ids")
        if len(refs) > 64:
            raise ValueError("signals.refs contains too many items")
        _validate_structured_json(decision.candidates, field="candidates")
        _validate_scalar(decision.choice, field="choice")
        if decision.budget_before is not None:
            _validate_structured_json(decision.budget_before, field="budget_before")
        if decision.budget_after is not None:
            _validate_structured_json(decision.budget_after, field="budget_after")
    if disposition == DecisionDisposition.EXECUTE:
        if not _is_structured_token(decision.correlation_id):
            raise ValueError("execute decision requires a correlation id")
    elif decision.correlation_id is not None:
        raise ValueError("non-execute decision must not reference a command")


def decision_fingerprint(
    decision: DecisionRecord,
    *,
    redact_fn: Callable[[Any], Any],
) -> str:
    body = {
        "kind": decision.kind,
        "disposition": _disposition_value(decision.disposition),
        "work_item_id": decision.work_item_id,
        "task_id": decision.task_id,
        "episode_id": decision.episode_id,
        "cause_id": decision.cause_id,
        "correlation_id": decision.correlation_id,
        "policy_version": decision.policy_version,
        "signals": redact_fn(decision.signals),
        "candidates": redact_fn(decision.candidates),
        "choice": redact_fn(decision.choice),
        "reason": redact_fn(decision.reason),
        "budget_before": redact_fn(decision.budget_before),
        "budget_after": redact_fn(decision.budget_after),
    }
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _validate_refs(value: Any, *, field: str) -> None:
    if not isinstance(value, list) or not all(_is_structured_token(v) for v in value):
        raise ValueError(f"{field} must be a list of references")
    if len(value) > 64:
        raise ValueError(f"{field} contains too many references")


def validate_command_event(event: EventEnvelope) -> None:
    if event.kind == "command.intent":
        required = {
            "command_kind",
            "target_ref",
            "idempotency_key_hash",
            "args_hash",
        }
        if set(event.payload) != required:
            raise ValueError("command.intent payload fields do not match the schema")
        if not _is_structured_token(event.payload["command_kind"]):
            raise ValueError("command_kind must be structured")
        if not _is_structured_token(event.payload["target_ref"]):
            raise ValueError("target_ref must be structured")
        for field in ("idempotency_key_hash", "args_hash"):
            value = event.payload[field]
            if not isinstance(value, str) or _HASH_TOKEN.fullmatch(value) is None:
                raise ValueError(f"{field} must be a sha256 reference")
    elif event.kind == "command.result":
        if not {"result_code", "evidence_refs"} <= set(event.payload) <= {
            "result_code",
            "evidence_refs",
            "usage_ref",
        }:
            raise ValueError("command.result payload fields do not match the schema")
        if not _is_structured_token(event.payload["result_code"]):
            raise ValueError("result_code must be structured")
        _validate_refs(event.payload["evidence_refs"], field="evidence_refs")
        if "usage_ref" in event.payload and not _is_structured_token(
            event.payload["usage_ref"]
        ):
            raise ValueError("usage_ref must be structured")
    elif event.kind == "command.unknown":
        if set(event.payload) != {"unknown_code", "evidence_refs"}:
            raise ValueError("command.unknown payload fields do not match the schema")
        if event.payload["unknown_code"] not in {
            "unknown_requires_reconcile",
            "unknown_requires_user_restart",
        }:
            raise ValueError("unknown_code is invalid")
        _validate_refs(event.payload["evidence_refs"], field="evidence_refs")
    else:
        raise ValueError("not a command event")
    if not _is_structured_token(event.cause_id):
        raise ValueError("command event requires a cause id")
    if not _is_structured_token(event.correlation_id):
        raise ValueError("command event requires a correlation id")


def validate_decision_intent_pair(
    decision: DecisionRecord, intent: EventEnvelope
) -> None:
    validate_decision(decision)
    if DecisionDisposition(_disposition_value(decision.disposition)) != DecisionDisposition.EXECUTE:
        raise ValueError("only execute decisions can have command intents")
    if intent.kind != "command.intent":
        raise ValueError("execute decision requires command.intent")
    validate_command_event(intent)
    if intent.cause_id != decision.decision_id:
        raise ValueError("command intent cause does not match decision")
    if intent.correlation_id != decision.correlation_id:
        raise ValueError("command intent correlation does not match decision")
