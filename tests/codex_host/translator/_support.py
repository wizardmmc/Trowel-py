from __future__ import annotations

import json
from pathlib import Path

_FIXTURES = Path(__file__).parents[1] / "fixtures"

# notifications 与 command-actions 是脱敏后的 Codex 0.144.0 真实录制。


def _notifications() -> list[dict]:

    return [
        json.loads(line)
        for line in (_FIXTURES / "notifications.jsonl").read_text().splitlines()
        if line.strip()
    ]


def _command_action_notifications() -> list[dict]:

    return [
        json.loads(line)
        for line in (_FIXTURES / "command-actions.jsonl").read_text().splitlines()
        if line.strip()
    ]


def _by_method(method: str) -> dict:

    for msg in _notifications():
        if msg["method"] == method:
            return msg
    raise AssertionError(f"no notification with method {method!r} in fixture")


# file-change fixture 录于 2026-07-19，来自真实 apply_patch 会话。


def _file_change_notifications(name: str) -> list[dict]:

    return [
        json.loads(line)
        for line in (_FIXTURES / name).read_text().splitlines()
        if line.strip()
    ]


def _file_change_msg(name: str, method: str, kind_type: str) -> dict:

    for msg in _file_change_notifications(name):
        if msg["method"] != method:
            continue
        changes = msg["params"]["item"]["changes"]
        if changes and changes[0]["kind"]["type"] == kind_type:
            return msg
    raise AssertionError(f"no {method} fileChange with kind {kind_type!r} in {name}")
