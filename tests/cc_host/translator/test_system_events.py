import logging
from typing import Any

import pytest

import trowel_py.cc_host.translator as translator_module
from trowel_py.cc_host.system_events import _as_int, translate_system_event
from trowel_py.cc_host.translator import Translator
from trowel_py.schemas.cc_host import (
    CompactBoundaryEvent,
    HookEvent,
    LocalCommandEvent,
    StatusEvent,
)


def _translate(
    event: dict[str, Any],
    *,
    logger: logging.Logger | None = None,
):
    return translate_system_event(
        event,
        as_text_fn=translator_module._as_text,
        logger=logger or logging.getLogger(__name__),
    )


def test_hook_started_translates():
    out = _translate(
        {
            "type": "system",
            "subtype": "hook_started",
            "hook_name": "PreToolUse",
            "outcome": None,
        }
    )

    assert out == [HookEvent(hook_name="PreToolUse", outcome=None)]


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (
            {"type": "system", "subtype": "status", "subtype2": "compacting"},
            "compacting",
        ),
        ({"type": "system", "subtype": "status", "stage": "running"}, "running"),
        ({"type": "system", "subtype": "status"}, "unknown"),
    ],
)
def test_status_uses_compatible_stage_fields(event, expected):
    out = _translate(event)

    assert out == [StatusEvent(stage=expected)]


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"trigger": "manual"}, "manual"),
        ({"trigger": 7}, None),
        ("invalid", None),
    ],
)
def test_compact_boundary_normalizes_metadata(metadata, expected):
    out = _translate(
        {
            "type": "system",
            "subtype": "compact_boundary",
            "compactMetadata": metadata,
        }
    )

    assert out == [CompactBoundaryEvent(trigger=expected)]


def test_local_command_output_uses_translator_text_flattening():
    out = _translate(
        {
            "type": "system",
            "subtype": "local_command_output",
            "content": [
                {"type": "text", "text": "first"},
                {"type": "image", "text": "ignored"},
                {"type": "text", "text": "second"},
            ],
        }
    )

    assert out == [LocalCommandEvent(content="first\nsecond")]


def test_unknown_system_subtype_is_logged(caplog):
    event_logger = logging.getLogger("tests.cc_host.system_events")

    with caplog.at_level(logging.DEBUG, logger=event_logger.name):
        out = _translate(
            {"type": "system", "subtype": "unexpected"},
            logger=event_logger,
        )

    assert out == []
    assert caplog.messages == ["unmapped CC system subtype dropped: 'unexpected'"]


def test_invalid_thinking_token_count_is_not_swallowed():
    with pytest.raises(ValueError, match="invalid literal"):
        _translate(
            {
                "type": "system",
                "subtype": "thinking_tokens",
                "estimated_tokens": "not-a-number",
            }
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (False, None),
        (True, None),
        (7, 7),
        (7.9, 7),
        ("8", 8),
        ("bad", None),
        (object(), None),
    ],
)
def test_as_int_boundary(value, expected):
    assert _as_int(value) == expected


def test_system_dispatch_keeps_translator_facade_identity():
    translator = Translator()
    handler = translator._dispatch["system"]

    assert handler.__self__ is translator
    assert handler.__func__ is Translator._on_system


def test_system_facade_delegates_current_cross_domain_dependencies(monkeypatch):
    captured = {}
    translator = Translator()

    def text_adapter(content):
        return f"text:{content}"

    delegated_logger = logging.getLogger("tests.cc_host.delegated")

    def fake_translate(event, *, as_text_fn, logger):
        captured.update(
            event=event,
            as_text_fn=as_text_fn,
            logger=logger,
        )
        return []

    monkeypatch.setattr(translator_module, "_as_text", text_adapter)
    monkeypatch.setattr(translator_module, "logger", delegated_logger)
    monkeypatch.setattr(translator_module, "translate_system_event", fake_translate)
    event = {"type": "system", "subtype": "status"}

    assert translator.translate(event) == []
    assert captured == {
        "event": event,
        "as_text_fn": text_adapter,
        "logger": delegated_logger,
    }
