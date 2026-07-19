"""Message classification and protocol constant tests.

Each case ties back to the JSON-RPC shapes in the 0.144.0 schema bundle
(see ``tests/codex_host/fixtures/schema-baseline-0.144.0.txt``): request = id+method,
notification = method only, response = id only (result or error).
"""

from __future__ import annotations

import pytest

from trowel_py.codex_host.protocol import (
    APP_SERVER_ARGS,
    KNOWN_SERVER_REQUEST_METHODS,
    SUPPORTED_CODEX_VERSION,
    ClientInfo,
    MessageKind,
    classify_server_message,
)


def test_supported_version_is_pinned_to_spike_baseline() -> None:
    """The version window matches the 2026-07-18 spike recording."""

    assert SUPPORTED_CODEX_VERSION == "0.144.0"


def test_app_server_args_force_stdio_and_disable_memories() -> None:
    """Spec §1: argv suffix is fixed — stdio transport, native memories off."""

    assert APP_SERVER_ARGS == ("app-server", "--stdio", "--disable", "memories")


def test_client_info_round_trips_to_dict() -> None:
    """clientInfo serialises to the exact three-key block app-server expects."""

    info = ClientInfo()
    assert info.as_dict() == {
        "name": "trowel_codex_host",
        "title": "Trowel Codex Host",
        "version": "0.1.0",
    }


def test_client_info_is_immutable() -> None:
    """Frozen dataclass — callers cannot mutate the shared default."""

    info = ClientInfo()
    with pytest.raises(Exception):
        info.name = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "message, expected",
    [
        ({"id": 1, "method": "x"}, MessageKind.SERVER_REQUEST),
        ({"id": "abc", "method": "x", "params": {}}, MessageKind.SERVER_REQUEST),
        ({"method": "item/completed", "params": {}}, MessageKind.NOTIFICATION),
        ({"method": "turn/completed"}, MessageKind.NOTIFICATION),
        ({"id": 1, "result": {}}, MessageKind.RESPONSE),
        ({"id": 1, "error": {"code": -1, "message": "boom"}}, MessageKind.RESPONSE),
        ({}, MessageKind.INVALID),
        ({"id": 1}, MessageKind.INVALID),
        ({"method": ""}, MessageKind.INVALID),
        ({"method": None}, MessageKind.INVALID),
        ("not a dict", MessageKind.INVALID),
        (None, MessageKind.INVALID),
        (42, MessageKind.INVALID),
    ],
)
def test_classify_server_message_buckets_every_shape(
    message: object, expected: MessageKind
) -> None:
    """The classifier matches the four JSON-RPC buckets, invalid otherwise."""

    assert classify_server_message(message) == expected


def test_known_server_request_methods_match_schema() -> None:
    """Baseline 0.144.0 schema advertises exactly these server request methods."""

    assert "item/commandExecution/requestApproval" in KNOWN_SERVER_REQUEST_METHODS
    assert "item/fileChange/requestApproval" in KNOWN_SERVER_REQUEST_METHODS
    assert "mcpServer/elicitation/request" in KNOWN_SERVER_REQUEST_METHODS
    assert "item/tool/requestUserInput" in KNOWN_SERVER_REQUEST_METHODS
    # The schema bundle lists 11 experimental + stable server request methods.
    assert len(KNOWN_SERVER_REQUEST_METHODS) == 11
