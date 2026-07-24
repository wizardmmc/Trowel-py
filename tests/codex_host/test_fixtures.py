"""Codex 录制 fixture 的消息分类、协议来源与隐私门禁。"""

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
    r"/Users/[A-Za-z0-9._-]+|/var/folders|"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
    re.IGNORECASE,
)


def _read_jsonl(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_initialize_response_fixture_classifies_as_response(fixtures_dir) -> None:
    messages = _read_jsonl(fixtures_dir / "initialize-response.jsonl")
    assert messages, "initialize-response fixture is empty"
    for message in messages:
        assert classify_server_message(message) is MessageKind.RESPONSE


def test_notifications_fixture_classifies_as_notification(fixtures_dir) -> None:
    messages = _read_jsonl(fixtures_dir / "notifications.jsonl")
    assert len(messages) >= 8
    for message in messages:
        assert classify_server_message(message) is MessageKind.NOTIFICATION


def test_server_request_approval_fixture_classifies_as_server_request(
    fixtures_dir,
) -> None:
    messages = _read_jsonl(fixtures_dir / "server-request-approval.jsonl")
    assert len(messages) == 1
    message = messages[0]
    assert classify_server_message(message) is MessageKind.SERVER_REQUEST
    assert message["method"] == "item/commandExecution/requestApproval"


def test_file_approval_fixture_classifies_as_server_request(fixtures_dir) -> None:
    messages = _read_jsonl(fixtures_dir / "server-request-file-approval-075.jsonl")
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
        "file-change-add-modify-076",
        "file-change-declined-076",
        "file-change-delete-076",
    ],
)
def test_fixtures_carry_no_secrets_or_user_paths(fixtures_dir, name: str) -> None:
    text = (fixtures_dir / f"{name}.jsonl").read_text()
    match = _SECRET_RE.search(text)
    assert match is None, f"{name}.jsonl contains a secret/path pattern: {match!r}"


def test_notification_methods_are_in_schema_baseline(fixtures_dir) -> None:
    baseline = (fixtures_dir / "schema-baseline-0.144.0.txt").read_text()
    messages = _read_jsonl(fixtures_dir / "notifications.jsonl")
    for message in messages:
        assert message["method"] in baseline, (
            f"fixture notification {message['method']!r} missing from schema baseline"
        )


def test_known_server_request_methods_are_in_schema_baseline(fixtures_dir) -> None:
    baseline = (fixtures_dir / "schema-baseline-0.144.0.txt").read_text()
    missing = [m for m in KNOWN_SERVER_REQUEST_METHODS if m not in baseline]
    assert missing == [], f"KNOWN_SERVER_REQUEST_METHODS not in baseline: {missing}"
