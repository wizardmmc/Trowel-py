"""JSON-RPC message classification and protocol constants.

The app-server wire format is JSON-RPC 2.0 with the ``"jsonrpc":"2.0"`` header
omitted on the wire (confirmed by the 2026-07-18 spike and the 0.144.0 schema).
Every server line therefore falls into exactly one bucket:

============  =========================================================
response       has ``id``, no ``method`` → completes a pending future
notification   has ``method``, no ``id`` → publish to subscribers
server_request has ``method`` and ``id`` → dispatch to a registered handler
invalid        anything else → record diagnostic, escalate if repeated
============  =========================================================

Field names and method strings below trace back to the schema bundle generated
by ``codex app-server generate-json-schema --experimental`` on 0.144.0 (kept
under ``tests/codex_host/fixtures/schema-baseline-0.144.0.txt``) and to the real
recordings in ``tests/codex_host/fixtures/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

# The Codex CLI version this transport was validated against. Spec §1 pins
# 0.144.0; do not change without re-running the spike and regenerating the
# schema baseline.
SUPPORTED_CODEX_VERSION = "0.144.0"

# Fixed argv suffix — spec §1. The ``--disable memories`` flag turns Codex
# native memories off so trowel owns the memory plane (M9 decision).
APP_SERVER_ARGS: tuple[str, ...] = ("app-server", "--stdio", "--disable", "memories")

# slice-078: the MCP server name trowel registers on a Codex thread to expose
# the memory read-path (search/read/outcome). Deliberately specific so it does
# not collide with a user-configured server in ~/.codex/config.toml; the
# isolation check (codex_host.mcp_isolation) refuses to create a memory-on
# session when a same-named server already exists in the user's config.
TROWEL_NOTE_SEARCH_SERVER_NAME = "trowel_note_search"

# Trowel identifies itself to the OpenAI Compliance Logs Platform via
# ``clientInfo.name`` (see app-server README → Initialization).
CLIENT_NAME = "trowel_codex_host"
CLIENT_TITLE = "Trowel Codex Host"
CLIENT_VERSION = "0.1.0"

# Server-initiated request method names we currently understand. Unknown
# methods are rejected by default (spec C-3) — this list only documents what
# the baseline schema advertises, it does not auto-enable handling.
KNOWN_SERVER_REQUEST_METHODS: frozenset[str] = frozenset(
    {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
        "item/tool/requestUserInput",
        "item/tool/call",
        "mcpServer/elicitation/request",
        "applyPatchApproval",
        "execCommandApproval",
        "attestation/generate",
        "currentTime/read",
        "account/chatgptAuthTokens/refresh",
    }
)


class MessageKind(str, Enum):
    """The four mutually-exclusive buckets a server line can fall into."""

    RESPONSE = "response"
    NOTIFICATION = "notification"
    SERVER_REQUEST = "server_request"
    INVALID = "invalid"


@dataclass(frozen=True)
class ClientInfo:
    """The ``initialize.params.clientInfo`` block sent on every connection.

    Attributes:
        name: Stable client identifier (Compliance Logs Platform key).
        title: Human-readable client name.
        version: Semver of the trowel codex_host client.
    """

    name: str = CLIENT_NAME
    title: str = CLIENT_TITLE
    version: str = CLIENT_VERSION

    def as_dict(self) -> dict[str, str]:
        """Return the JSON-ready ``clientInfo`` object."""

        return {"name": self.name, "title": self.title, "version": self.version}


def classify_server_message(message: Any) -> MessageKind:
    """Bucket one parsed server message.

    Args:
        message: The parsed JSON object read from app-server stdout.

    Returns:
        The :class:`MessageKind`. ``INVALID`` covers non-dicts, dicts missing
        the required keys, and any other shape the JSON-RPC schema does not
        allow.
    """

    if not isinstance(message, Mapping):
        return MessageKind.INVALID
    has_id = "id" in message
    has_method = isinstance(message.get("method"), str) and bool(message.get("method"))
    if has_id and has_method:
        return MessageKind.SERVER_REQUEST
    if has_method and not has_id:
        return MessageKind.NOTIFICATION
    if has_id and not has_method and ("result" in message or "error" in message):
        # JSON-RPC requires a result (success) or error object; a bare ``{"id": X}``
        # is neither and is treated as invalid rather than a half-formed response.
        return MessageKind.RESPONSE
    return MessageKind.INVALID
