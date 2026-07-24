from __future__ import annotations

import json
from pathlib import Path

from trowel_py.model_os.context_observer import (
    NormalizedCcEvent,
)

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "context_observer"
FIXTURES_CC = FIXTURES_ROOT / "cc"
FIXTURES_CODEX = FIXTURES_ROOT / "codex"

_NO_USAGE = object()


def cc_asst(
    *,
    message_id: str,
    model: str = "glm-5.2",
    input_tokens: int = 0,
    cache_creation: int = 0,
    cache_read: int = 0,
    output_tokens: int = 0,
    timestamp: str = "2026-07-21T00:00:00Z",
    turn_id: str | None = None,
    usage: object = _NO_USAGE,
) -> NormalizedCcEvent:
    """未传 usage 时按 token 参数构造；空字典与 None 保留各自语义。"""

    if usage is _NO_USAGE:
        usage = {
            "input_tokens": input_tokens,
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
            "output_tokens": output_tokens,
        }
    return NormalizedCcEvent(
        type="assistant",
        subtype=None,
        timestamp=timestamp,
        message_id=message_id,
        model=model,
        usage=usage,
        turn_id=turn_id,
    )


def cc_boundary(timestamp: str = "2026-07-21T00:00:01Z") -> NormalizedCcEvent:
    return NormalizedCcEvent(
        type="system",
        subtype="compact_boundary",
        timestamp=timestamp,
        message_id=None,
        model=None,
        usage=None,
        turn_id=None,
    )


def force_window(window: int | None):
    return lambda _model: window


def _load_cc_fixture(name: str) -> list[NormalizedCcEvent]:
    events: list[NormalizedCcEvent] = []
    for raw in (FIXTURES_CC / name).read_text().splitlines():
        raw = raw.strip()
        if not raw:
            continue
        o = json.loads(raw)
        events.append(
            NormalizedCcEvent(
                type=o["type"],
                subtype=o.get("subtype"),
                timestamp=o.get("timestamp"),
                message_id=o.get("message_id"),
                model=o.get("model"),
                usage=o.get("usage"),
                turn_id=o.get("turn_id"),
                compact_trigger=o.get("compact_trigger"),
                compact_pre_tokens=o.get("compact_pre_tokens"),
                compact_post_tokens=o.get("compact_post_tokens"),
            )
        )
    return events


def _load_codex_fixture(name: str) -> list[dict]:
    events: list[dict] = []
    for raw in (FIXTURES_CODEX / name).read_text().splitlines():
        raw = raw.strip()
        if raw:
            events.append(json.loads(raw))
    return events
