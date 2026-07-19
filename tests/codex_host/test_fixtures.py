"""Fixture provenance + schema-baseline traceability (spec §4 + C-4).

The recordings under ``fixtures/`` are not hand-written — they are redacted
slices of the real 2026-07-18 spike. These tests pin that contract:

* every fixture line classifies into the JSON-RPC bucket its filename claims;
* no fixture carries a secret or a user-specific path (C-6);
* every notification method a fixture uses is present in the schema baseline
  generated from ``codex app-server generate-json-schema`` on 0.144.0 (C-4 —
  field traceability, not memory);
* the method set ``protocol.KNOWN_SERVER_REQUEST_METHODS`` is reflected in the
  same baseline, so the two cannot drift silently.
"""

from __future__ import annotations

import json
import re

import pytest

from trowel_py.codex_host.protocol import (
    KNOWN_SERVER_REQUEST_METHODS,
    MessageKind,
    classify_server_message,
)

_SECRET_RE = re.compile(
    r"Bearer|sk-[A-Za-z0-9]{8}|authorization|api[_-]?key|access[_-]?token|"
    r"/Users/hamxf|/var/folders",
    re.IGNORECASE,
)


def _read_jsonl(path) -> list[dict]:
    """Parse every non-blank line of a JSONL fixture into a dict."""

    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_initialize_response_fixture_classifies_as_response(fixtures_dir) -> None:
    """The initialize recording is a JSON-RPC response (id + result)."""

    messages = _read_jsonl(fixtures_dir / "initialize-response.jsonl")
    assert messages, "initialize-response fixture is empty"
    for message in messages:
        assert classify_server_message(message) is MessageKind.RESPONSE


def test_notifications_fixture_classifies_as_notification(fixtures_dir) -> None:
    """Every recorded notification has method + params, no id."""

    messages = _read_jsonl(fixtures_dir / "notifications.jsonl")
    assert len(messages) >= 8  # the pack ships ≥8 distinct method shapes
    for message in messages:
        assert classify_server_message(message) is MessageKind.NOTIFICATION


def test_server_request_approval_fixture_classifies_as_server_request(
    fixtures_dir,
) -> None:
    """The approval recording is a bidirectional server request (method + id)."""

    messages = _read_jsonl(fixtures_dir / "server-request-approval.jsonl")
    assert len(messages) == 1
    message = messages[0]
    assert classify_server_message(message) is MessageKind.SERVER_REQUEST
    assert message["method"] == "item/commandExecution/requestApproval"


def test_file_approval_fixture_classifies_as_server_request(fixtures_dir) -> None:
    """The slice-075 file recording is a real bidirectional request."""

    messages = _read_jsonl(
        fixtures_dir / "server-request-file-approval-075.jsonl"
    )
    assert len(messages) == 1
    message = messages[0]
    assert classify_server_message(message) is MessageKind.SERVER_REQUEST
    assert message["method"] == "item/fileChange/requestApproval"


@pytest.mark.parametrize(
    "name",
    [
        "initialize-response",
        "notifications",
        "server-request-approval",
        "server-request-file-approval-075",
    ],
)
def test_fixtures_carry_no_secrets_or_user_paths(fixtures_dir, name: str) -> None:
    """C-6: no auth, token or user-specific path survives into a fixture."""

    text = (fixtures_dir / f"{name}.jsonl").read_text()
    match = _SECRET_RE.search(text)
    assert match is None, f"{name}.jsonl contains a secret/path pattern: {match!r}"


def test_notification_methods_are_in_schema_baseline(fixtures_dir) -> None:
    """C-4: every recorded notification method is advertised by the 0.144.0 schema."""

    baseline = (fixtures_dir / "schema-baseline-0.144.0.txt").read_text()
    messages = _read_jsonl(fixtures_dir / "notifications.jsonl")
    for message in messages:
        assert message["method"] in baseline, (
            f"fixture notification {message['method']!r} missing from schema baseline"
        )


def test_known_server_request_methods_are_in_schema_baseline(fixtures_dir) -> None:
    """The protocol constant set must match the schema baseline (anti-drift)."""

    baseline = (fixtures_dir / "schema-baseline-0.144.0.txt").read_text()
    missing = [m for m in KNOWN_SERVER_REQUEST_METHODS if m not in baseline]
    assert missing == [], f"KNOWN_SERVER_REQUEST_METHODS not in baseline: {missing}"
