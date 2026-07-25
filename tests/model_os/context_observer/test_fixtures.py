from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from tests.model_os.context_observer.support import FIXTURES_ROOT

_FIXTURES = (
    "cc/glm-5.2-multi-turn.jsonl",
    "cc/glm-5.2-compact-boundary.jsonl",
    "cc/glm-5.2-subagent-transcript.jsonl",
    "codex/usage_compact_sequence.jsonl",
)
_ALLOWED_KEYS = {
    "cache_creation",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "compact_post_tokens",
    "compact_pre_tokens",
    "compact_trigger",
    "ephemeral_1h_input_tokens",
    "ephemeral_5m_input_tokens",
    "inference_geo",
    "input_tokens",
    "iterations",
    "last",
    "message_id",
    "model",
    "model_context_window",
    "output_tokens",
    "payload",
    "phase",
    "server_tool_use",
    "service_tier",
    "speed",
    "subtype",
    "timestamp",
    "total",
    "totalTokens",
    "turn_id",
    "type",
    "usage",
    "web_fetch_requests",
    "web_search_requests",
}
_PRIVATE_VALUE = re.compile(
    r"Bearer|sk-[A-Za-z0-9]{8}|authorization|api[_-]?key|access[_-]?token|"
    r"/Users/[A-Za-z0-9._-]+|/home/[A-Za-z0-9._-]+|/var/folders|"
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
    re.IGNORECASE,
)
_ENUM_VALUES = {
    "compact_trigger": {"manual"},
    "inference_geo": {""},
    "model": {"glm-5.2"},
    "phase": {"completed"},
    "service_tier": {"standard"},
    "speed": {"standard"},
    "subtype": {"compact_boundary"},
    "type": {"assistant", "compaction", "last-prompt", "system", "usage_updated"},
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _all_keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _all_keys(item)}
    return set()


@pytest.mark.parametrize("relative_path", _FIXTURES)
def test_shared_fixture_is_minimal_and_deidentified(relative_path: str) -> None:
    path = FIXTURES_ROOT / relative_path
    text = path.read_text()
    events = _read_jsonl(path)

    assert events
    assert _all_keys(events) <= _ALLOWED_KEYS
    assert _PRIVATE_VALUE.search(text) is None
    for event in events:
        for key, value in _string_fields(event):
            assert len(value) <= 64
            if key == "message_id":
                assert re.fullmatch(r"message-\d+", value)
            elif key == "turn_id":
                assert re.fullmatch(r"turn-\d+", value)
            elif key == "timestamp":
                assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value)
            else:
                assert value in _ENUM_VALUES[key]


def _string_fields(value: Any) -> list[tuple[str, str]]:
    if isinstance(value, dict):
        return [
            item
            for key, child in value.items()
            for item in (
                [(key, child)] if isinstance(child, str) else _string_fields(child)
            )
        ]
    if isinstance(value, list):
        return [item for child in value for item in _string_fields(child)]
    return []
