from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any

import pytest

from trowel_py.codex_host import translator
from trowel_py.codex_host.translator import CodexTranslator


def test_command_mcp_facades_keep_complete_contracts() -> None:
    expected = {
        "_mcp_tool_name": "(server: 'Any', tool: 'Any') -> 'str'",
        "_command_actions": (
            "(item: 'Mapping[str, Any]', method: 'str') -> 'tuple[dict[str, Any], ...]'"
        ),
        "_command_started_item": (
            "(self, params: 'Mapping[str, Any]', item: 'Mapping[str, Any]') "
            "-> 'TranslatedItem'"
        ),
        "_command_completed_item": (
            "(self, params: 'Mapping[str, Any]', item: 'Mapping[str, Any]') "
            "-> 'TranslatedItem'"
        ),
        "_mcp_tool_started_item": (
            "(self, params: 'Mapping[str, Any]', item: 'Mapping[str, Any]') "
            "-> 'TranslatedItem'"
        ),
        "_mcp_tool_completed_item": (
            "(self, params: 'Mapping[str, Any]', item: 'Mapping[str, Any]') "
            "-> 'TranslatedItem'"
        ),
    }

    for name, signature in expected.items():
        facade = (
            getattr(translator, name)
            if name in {"_mcp_tool_name", "_command_actions"}
            else getattr(CodexTranslator, name)
        )
        assert str(inspect.signature(facade)) == signature
        assert facade.__module__ == translator.__name__
        expected_qualname = (
            name
            if name in {"_mcp_tool_name", "_command_actions"}
            else f"CodexTranslator.{name}"
        )
        assert facade.__qualname__ == expected_qualname
        assert facade.__defaults__ is None
        assert facade.__kwdefaults__ is None


def test_command_mcp_function_facades_inject_current_globals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}
    tool_name_result = object()
    actions_result = object()

    def run_tool_name(*args: Any, **kwargs: Any) -> Any:
        seen["tool_name"] = (args, kwargs)
        return tool_name_result

    def run_actions(*args: Any, **kwargs: Any) -> Any:
        seen["actions"] = (args, kwargs)
        return actions_result

    monkeypatch.setattr(translator, "_run_mcp_tool_name", run_tool_name)
    monkeypatch.setattr(translator, "_run_command_actions", run_actions)

    require_fn = object()
    isinstance_fn = object()
    list_type = object()
    mapping_type = object()
    str_type = object()
    protocol_violation_type = object()
    dict_type = object()
    action_fields = object()
    tuple_type = object()
    monkeypatch.setattr(translator, "_require", require_fn)
    monkeypatch.setattr(translator, "isinstance", isinstance_fn, raising=False)
    monkeypatch.setattr(translator, "list", list_type, raising=False)
    monkeypatch.setattr(translator, "Mapping", mapping_type)
    monkeypatch.setattr(translator, "str", str_type, raising=False)
    monkeypatch.setattr(
        translator,
        "ProtocolViolationError",
        protocol_violation_type,
    )
    monkeypatch.setattr(translator, "dict", dict_type, raising=False)
    monkeypatch.setattr(translator, "_COMMAND_ACTION_FIELDS", action_fields)
    monkeypatch.setattr(translator, "tuple", tuple_type, raising=False)

    server = object()
    tool = object()
    item = object()
    assert translator._mcp_tool_name(server, tool) is tool_name_result
    assert translator._command_actions(item, "item/started") is actions_result

    assert seen == {
        "tool_name": (
            (server, tool),
            {
                "isinstance_fn": isinstance_fn,
                "str_type": str_type,
            },
        ),
        "actions": (
            (item, "item/started"),
            {
                "require_fn": require_fn,
                "isinstance_fn": isinstance_fn,
                "list_type": list_type,
                "mapping_type": mapping_type,
                "str_type": str_type,
                "protocol_violation_type": protocol_violation_type,
                "dict_type": dict_type,
                "action_fields": action_fields,
                "tuple_type": tuple_type,
            },
        ),
    }


def test_command_mcp_method_facades_inject_current_globals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}
    results = {
        name: object()
        for name in (
            "command_started_item",
            "command_completed_item",
            "mcp_tool_started_item",
            "mcp_tool_completed_item",
        )
    }

    def recorder(name: str):
        def record(*args: Any, **kwargs: Any) -> Any:
            seen[name] = (args, kwargs)
            return results[name]

        return record

    for name in results:
        monkeypatch.setattr(translator, f"_run_{name}", recorder(name))

    translated_item_type = object()
    tool_started_type = object()
    tool_completed_type = object()
    as_str_fn = object()
    require_fn = object()
    immutable_payload_fn = object()
    item_command = object()
    item_mcp_tool = object()
    command_actions_fn = object()
    mcp_tool_name_fn = object()
    monkeypatch.setattr(translator, "TranslatedItem", translated_item_type)
    monkeypatch.setattr(
        translator,
        "CodexEventType",
        SimpleNamespace(
            TOOL_STARTED=tool_started_type,
            TOOL_COMPLETED=tool_completed_type,
        ),
    )
    monkeypatch.setattr(translator, "_as_str", as_str_fn)
    monkeypatch.setattr(translator, "_require", require_fn)
    monkeypatch.setattr(translator, "immutable_payload", immutable_payload_fn)
    monkeypatch.setattr(translator, "_ITEM_COMMAND", item_command)
    monkeypatch.setattr(translator, "_ITEM_MCP_TOOL", item_mcp_tool)
    monkeypatch.setattr(translator, "_command_actions", command_actions_fn)
    monkeypatch.setattr(translator, "_mcp_tool_name", mcp_tool_name_fn)

    instance = CodexTranslator()
    params = object()
    item = object()
    assert (
        instance._command_started_item(params, item) is results["command_started_item"]
    )
    assert (
        instance._command_completed_item(params, item)
        is results["command_completed_item"]
    )
    assert (
        instance._mcp_tool_started_item(params, item)
        is results["mcp_tool_started_item"]
    )
    assert (
        instance._mcp_tool_completed_item(params, item)
        is results["mcp_tool_completed_item"]
    )

    shared = {
        "translated_item_type": translated_item_type,
        "as_str_fn": as_str_fn,
        "require_fn": require_fn,
        "immutable_payload_fn": immutable_payload_fn,
    }
    assert seen == {
        "command_started_item": (
            (params, item),
            {
                **shared,
                "event_type": tool_started_type,
                "item_kind": item_command,
                "command_actions_fn": command_actions_fn,
            },
        ),
        "command_completed_item": (
            (params, item),
            {
                **shared,
                "event_type": tool_completed_type,
                "item_kind": item_command,
                "command_actions_fn": command_actions_fn,
            },
        ),
        "mcp_tool_started_item": (
            (params, item),
            {
                **shared,
                "event_type": tool_started_type,
                "item_kind": item_mcp_tool,
                "mcp_tool_name_fn": mcp_tool_name_fn,
            },
        ),
        "mcp_tool_completed_item": (
            (params, item),
            {
                **shared,
                "event_type": tool_completed_type,
                "item_kind": item_mcp_tool,
                "mcp_tool_name_fn": mcp_tool_name_fn,
            },
        ),
    }
