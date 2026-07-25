"""认知信号历史的 SQLite 可重建查询视图。"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from dataclasses import replace
from typing import Any, Mapping

from trowel_py.model_os.cognitive_signals import (
    CognitiveSignal,
    PendingLostPayload,
    SignalFamily,
    SignalKind,
    PendingState,
    SignalPage,
    StoredCognitiveSignal,
    signal_from_dict,
    unknown_signal_from_dict,
)


_CREATE_TABLE_SQL = """CREATE TABLE IF NOT EXISTS cognitive_signal_projection (
    signal_id TEXT PRIMARY KEY,
    event_seq INTEGER NOT NULL UNIQUE,
    task_id TEXT,
    episode_id TEXT,
    attempt_id TEXT NOT NULL,
    comparison_key_hash TEXT NOT NULL,
    kind TEXT NOT NULL,
    subtype TEXT NOT NULL,
    provenance TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    validity_kind TEXT NOT NULL,
    valid_until TEXT,
    signal_json TEXT NOT NULL
);"""

_CREATE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_events_reducer_page ON events(seq) "
    "WHERE kind NOT IN ('cognitive_signal.recorded', "
    "'cognitive_signal.late_signal_rejected');",
    "CREATE INDEX IF NOT EXISTS idx_cognitive_signals_task_page "
    "ON cognitive_signal_projection(task_id, event_seq);",
    "CREATE INDEX IF NOT EXISTS idx_cognitive_signals_episode_page "
    "ON cognitive_signal_projection(episode_id, event_seq);",
    "CREATE INDEX IF NOT EXISTS idx_cognitive_signals_attempt "
    "ON cognitive_signal_projection(attempt_id, event_seq);",
    "CREATE INDEX IF NOT EXISTS idx_cognitive_signals_comparison "
    "ON cognitive_signal_projection(comparison_key_hash, event_seq);",
)

CREATE_PROJECTION_SQL = "\n\n".join((_CREATE_TABLE_SQL, *_CREATE_INDEX_SQL))

_MAX_PAGE_SIZE = 200


def migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
    ensure_projection_schema(conn)
    conn.execute(
        "UPDATE meta SET value=? WHERE key='schema_version'",
        ("7",),
    )


def ensure_projection_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE_TABLE_SQL)
    for statement in _CREATE_INDEX_SQL:
        conn.execute(statement)


def insert_projection_row(
    conn: sqlite3.Connection,
    *,
    event_seq: int,
    event_payload: Mapping[str, Any],
) -> None:
    signal = _required_mapping(event_payload, "signal")
    binding = _required_mapping(event_payload, "binding")
    kind = _required_mapping(signal, "kind")
    validity = _required_mapping(signal, "validity")
    comparison = _required_mapping(signal, "comparison_key")
    signal_json = json.dumps(
        signal,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    values = (
        str(signal["signal_id"]),
        event_seq,
        _optional_str(binding.get("task_id")),
        _optional_str(binding.get("episode_id")),
        str(signal["attempt_id"]),
        _comparison_hash(comparison),
        str(kind["family"]),
        str(kind["subtype"]),
        str(signal["provenance"]),
        str(signal["observed_at"]),
        str(signal["recorded_at"]),
        str(validity["kind"]),
        _optional_str(validity.get("valid_until")),
        signal_json,
    )
    try:
        conn.execute(
            "INSERT INTO cognitive_signal_projection ("
            "signal_id, event_seq, task_id, episode_id, attempt_id, "
            "comparison_key_hash, kind, subtype, provenance, observed_at, "
            "recorded_at, validity_kind, valid_until, signal_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
    except sqlite3.IntegrityError:
        row = conn.execute(
            "SELECT event_seq, signal_json FROM cognitive_signal_projection "
            "WHERE signal_id=?",
            (values[0],),
        ).fetchone()
        if row is None or int(row["event_seq"]) != event_seq or row["signal_json"] != signal_json:
            raise ValueError(
                f"projection identity conflict for signal {values[0]!r}"
            )


def read_signal_from_event_payload(event_payload: Mapping[str, Any]) -> CognitiveSignal:
    signal = _required_mapping(event_payload, "signal")
    return signal_from_dict(signal)


def read_signal_page(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    as_of: str,
    limit: int,
    cursor: str | None,
) -> SignalPage:
    if not task_id:
        raise ValueError("task_id is required")
    if not 1 <= limit <= _MAX_PAGE_SIZE:
        raise ValueError(f"limit must be between 1 and {_MAX_PAGE_SIZE}")
    after_seq = _decode_cursor(cursor, task_id=task_id, as_of=as_of) if cursor else 0
    rows = conn.execute(
        "SELECT event_seq, episode_id, signal_json "
        "FROM cognitive_signal_projection "
        "WHERE task_id=? AND event_seq>? AND julianday(observed_at)<=julianday(?) "
        "AND (validity_kind!='state_sample' "
        "OR julianday(valid_until)>=julianday(?)) "
        "ORDER BY event_seq LIMIT ?",
        (task_id, after_seq, as_of, as_of, limit + 1),
    ).fetchall()
    page_rows = rows[:limit]
    items: list[StoredCognitiveSignal] = []
    for row in page_rows:
        value = json.loads(row["signal_json"])
        try:
            kind = _required_mapping(value, "kind")
            SignalKind(SignalFamily(kind["family"]), str(kind["subtype"]))
        except (KeyError, ValueError):
            items.append(unknown_signal_from_dict(value))
            continue
        signal = signal_from_dict(value)
        if isinstance(signal.validity, PendingState) and (
            signal.validity.terminal_event_ref is None
        ):
            terminal_ref = _pending_terminal_ref(
                conn,
                episode_id=row["episode_id"],
                after_seq=int(row["event_seq"]),
                as_of=as_of,
                resolution_state=(
                    signal.payload.resolution_state
                    if isinstance(signal.payload, PendingLostPayload)
                    else None
                ),
            )
            if terminal_ref is not None:
                signal = replace(signal, validity=PendingState(terminal_ref))
        items.append(signal)
    next_cursor = None
    if len(rows) > limit and page_rows:
        next_cursor = _encode_cursor(
            task_id=task_id,
            as_of=as_of,
            last_event_seq=int(page_rows[-1]["event_seq"]),
        )
    return SignalPage(tuple(items), next_cursor)


def rebuild_projection(conn: sqlite3.Connection, *, event_kind: str) -> int:
    ensure_projection_schema(conn)
    conn.execute("DELETE FROM cognitive_signal_projection")
    rows = conn.execute(
        "SELECT seq, payload FROM events WHERE kind=? ORDER BY seq",
        (event_kind,),
    ).fetchall()
    for row in rows:
        payload = json.loads(row["payload"])
        insert_projection_row(conn, event_seq=int(row["seq"]), event_payload=payload)
    return len(rows)


def _pending_terminal_ref(
    conn: sqlite3.Connection,
    *,
    episode_id: str | None,
    after_seq: int,
    as_of: str,
    resolution_state: str | None,
) -> str | None:
    if episode_id is None or resolution_state is None:
        return None
    resolution_kinds = [
        "episode.reconcile_resolved",
        "episode.closed",
        "episode.failed",
    ]
    if resolution_state == "requires_user_restart":
        resolution_kinds.append("episode.wait_resolved")
    placeholders = ", ".join("?" for _ in resolution_kinds)
    row = conn.execute(
        "SELECT event_id FROM events WHERE episode_id=? AND seq>? "
        f"AND kind IN ({placeholders}) AND julianday(occurred_at)<=julianday(?) "
        "ORDER BY seq LIMIT 1",
        (episode_id, after_seq, *resolution_kinds, as_of),
    ).fetchone()
    return None if row is None else str(row["event_id"])


def _comparison_hash(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        {
            "runtime": value.get("runtime"),
            "attempt_category": value.get("attempt_category"),
            "task_id": value.get("task_id"),
            "target_ref": value.get("target_ref"),
            "validator_intent_id": value.get("validator_intent_id"),
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _encode_cursor(*, task_id: str, as_of: str, last_event_seq: int) -> str:
    body = {"task_id": task_id, "as_of": as_of, "last_event_seq": last_event_seq}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    body["checksum"] = hashlib.sha256(
        ("cognitive-signal-cursor-v1\x1f" + canonical).encode()
    ).hexdigest()[:16]
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str, *, task_id: str, as_of: str) -> int:
    try:
        padding = "=" * (-len(cursor) % 4)
        body = json.loads(base64.urlsafe_b64decode(cursor + padding))
        checksum = body.pop("checksum")
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
        expected = hashlib.sha256(
            ("cognitive-signal-cursor-v1\x1f" + canonical).encode()
        ).hexdigest()[:16]
        if checksum != expected or body["task_id"] != task_id or body["as_of"] != as_of:
            raise ValueError
        last_event_seq = int(body["last_event_seq"])
        if last_event_seq < 0:
            raise ValueError
        return last_event_seq
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid cognitive signal cursor") from exc


def _required_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise ValueError(f"{key} must be an object")
    return item


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)
