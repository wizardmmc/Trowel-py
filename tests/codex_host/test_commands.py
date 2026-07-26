from __future__ import annotations

from trowel_py.codex_host.commands import (
    RESERVED_CODEX_COMMANDS,
    command_roster,
    reserved_command_name,
)


def test_validated_version_exposes_only_supported_native_commands() -> None:
    roster = command_roster("0.144.0")

    assert [command["name"] for command in roster] == [
        "status",
        "compact",
        "review",
        "goal",
        "diff",
    ]
    assert roster[0]["available_while_running"] is True
    assert roster[1]["available_while_running"] is False
    assert roster[2]["available_while_running"] is False
    assert all(command["source"] == "codex" for command in roster)


def test_unvalidated_version_does_not_guess_command_support() -> None:
    assert command_roster("0.145.0") == []
    assert command_roster(None) == []


def test_reserved_command_detection_never_treats_prefixes_as_commands() -> None:
    assert RESERVED_CODEX_COMMANDS == {
        "status",
        "compact",
        "review",
        "goal",
        "diff",
    }
    assert reserved_command_name("/review") == "review"
    assert reserved_command_name("  /review   ") == "review"
    assert reserved_command_name("/review focus on auth") == "review"
    assert reserved_command_name("/reviewer") is None
    assert reserved_command_name("explain /review") is None
