from __future__ import annotations

from pathlib import Path

from trowel_py.cc_host import history
from trowel_py.schemas.cc_host import UserEvent
from tests.cc_host.history._support import (
    _user_list_text,
    _write_jsonl,
)

# CC 会把内部注入也持久化为 user 行，历史回放必须与实时路径一样只呈现真实输入。


def test_parse_history_skips_ismeta_user_row(fake_projects: Path) -> None:
    _write_jsonl(
        fake_projects / "abc-123.jsonl",
        [
            _user_list_text(
                "Base directory for this skill: /Users/x/.claude/skills/grill-me\n\n"
                "Interview me relentlessly about every aspect of this plan.",
                is_meta=True,
            )
        ],
    )
    events = history.parse_history("/workdir", "abc-123")
    assert [e for e in events if isinstance(e, UserEvent)] == []


def test_parse_history_command_tags_restore_slash_name_args(
    fake_projects: Path,
) -> None:
    _write_jsonl(
        fake_projects / "abc-123.jsonl",
        [
            _user_list_text(
                "<command-message>grill-me</command-message>\n"
                "<command-name>/grill-me</command-name>\n"
                "<command-args>修复bug</command-args>"
            )
        ],
    )
    events = history.parse_history("/workdir", "abc-123")
    user_evs = [e for e in events if isinstance(e, UserEvent)]
    assert len(user_evs) == 1
    assert user_evs[0].text == "/grill-me 修复bug"


def test_parse_history_skill_trigger_prompt_restores_slash(
    fake_projects: Path,
) -> None:
    _write_jsonl(
        fake_projects / "abc-123.jsonl",
        [_user_list_text("Use the Skill tool with skill='grill-me'. 修复bug")],
    )
    events = history.parse_history("/workdir", "abc-123")
    user_evs = [e for e in events if isinstance(e, UserEvent)]
    assert len(user_evs) == 1
    assert user_evs[0].text == "/grill-me 修复bug"


def test_parse_history_local_command_stdout_not_rendered(
    fake_projects: Path,
) -> None:
    _write_jsonl(
        fake_projects / "abc-123.jsonl",
        [
            _user_list_text(
                "<local-command-stdout>Set model to glm-5.1 and saved as "
                "your default for new sessions</local-command-stdout>"
            )
        ],
    )
    events = history.parse_history("/workdir", "abc-123")
    assert [e for e in events if isinstance(e, UserEvent)] == []


def test_parse_history_real_user_text_unchanged(fake_projects: Path) -> None:
    _write_jsonl(
        fake_projects / "abc-123.jsonl",
        [_user_list_text("修复重载渲染的几个 bug")],
    )
    events = history.parse_history("/workdir", "abc-123")
    user_evs = [e for e in events if isinstance(e, UserEvent)]
    assert len(user_evs) == 1
    assert user_evs[0].text == "修复重载渲染的几个 bug"
