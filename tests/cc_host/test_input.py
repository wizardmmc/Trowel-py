"""Tests for cc_host.input — the slash-handling input layer.

CC's stream-json input has no command channel; slash is REPL-only. So trowel
intercepts `/<name>` itself, classifies it, and hands CC only plain text. This
is mandatory: smoke A proved that passing a raw `/skillname` makes the model
guess from the skill directory instead of actually invoking the Skill tool.
"""
from pathlib import Path

import pytest

from trowel_py.cc_host.input import (
    classify_input,
    expand_command_file,
    skill_trigger_prompt,
    SendText,
    LocalCommand,
    RestartSession,
    UnsupportedSlash,
)


class TestPlainText:
    def test_plain_text_is_sent_as_is(self, tmp_path: Path):
        action = classify_input("hello world", workdir=tmp_path)
        assert isinstance(action, SendText)
        assert action.text == "hello world"

    def test_leading_slash_only_is_plain(self, tmp_path: Path):
        # "/" alone is not a command; send as text
        action = classify_input("/", workdir=tmp_path)
        assert isinstance(action, SendText)


class TestSkillTrigger:
    def test_skillname_becomes_trigger_prompt(self, tmp_path: Path):
        action = classify_input("/test-skill", workdir=tmp_path)
        assert isinstance(action, SendText)
        assert "skill='test-skill'" in action.text
        assert "Skill" in action.text

    def test_skillname_with_args_passes_args_through(self, tmp_path: Path):
        action = classify_input("/deep-research find me a thing", workdir=tmp_path)
        assert isinstance(action, SendText)
        assert "find me a thing" in action.text
        assert "skill='deep-research'" in action.text

    def test_plugin_namespaced_skill_preserved(self, tmp_path: Path):
        action = classify_input("/myplugin:skill do x", workdir=tmp_path)
        assert isinstance(action, SendText)
        assert "skill='myplugin:skill'" in action.text


class TestCustomCommandExpansion:
    def _write_echo(self, dirp: Path, body: str) -> None:
        dirp.mkdir(parents=True, exist_ok=True)
        (dirp / "echo.md").write_text(body)

    def test_project_command_expands_arguments(self, tmp_path: Path):
        self._write_echo(
            tmp_path / ".claude" / "commands",
            "---\ndescription: echo\n---\nRespond with exactly: ECHO$ARGUMENTS\n",
        )
        action = classify_input("/echo hello", workdir=tmp_path)
        assert isinstance(action, SendText)
        assert "ECHOhello" in action.text
        # frontmatter stripped
        assert "description" not in action.text

    def test_user_level_command_also_resolved(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        user_cmds = tmp_path / "userhome" / ".claude" / "commands"
        self._write_echo(user_cmds, "---\ndescription: echo\n---\nbody $ARGUMENTS end\n")
        monkeypatch.setattr("trowel_py.cc_host.input.user_commands_dir", lambda: user_cmds)
        # no project command, falls through to user-level
        action = classify_input("/echo hi", workdir=tmp_path / "wd")
        assert isinstance(action, SendText)
        assert "body hi end" in action.text

    def test_command_takes_precedence_over_skill_trigger(self, tmp_path: Path):
        # if a command file exists for the name, it wins over generic skill trigger
        self._write_echo(tmp_path / ".claude" / "commands", "---\n---\nCOMMAND_BODY\n")
        action = classify_input("/echo whatever", workdir=tmp_path)
        assert isinstance(action, SendText)
        assert "COMMAND_BODY" in action.text
        assert "skill='echo'" not in action.text


class TestBuiltinCommands:
    def test_cost_is_local_command(self, tmp_path: Path):
        action = classify_input("/cost", workdir=tmp_path)
        assert isinstance(action, LocalCommand)
        assert action.kind == "cost"

    def test_status_is_local_command(self, tmp_path: Path):
        action = classify_input("/status", workdir=tmp_path)
        assert isinstance(action, LocalCommand)
        assert action.kind == "status"

    def test_effort_requests_restart(self, tmp_path: Path):
        action = classify_input("/effort low", workdir=tmp_path)
        assert isinstance(action, RestartSession)
        assert action.effort == "low"
        assert action.model is None

    def test_model_requests_restart(self, tmp_path: Path):
        action = classify_input("/model glm-5.1", workdir=tmp_path)
        assert isinstance(action, RestartSession)
        assert action.model == "glm-5.1"
        assert action.effort is None

    def test_compress_is_unsupported(self, tmp_path: Path):
        action = classify_input("/compress", workdir=tmp_path)
        assert isinstance(action, UnsupportedSlash)
        assert action.name == "compress"

    def test_builtin_takes_precedence_over_command_file(self, tmp_path: Path):
        # even if someone ships a cost.md, /cost stays a local command
        (tmp_path / ".claude" / "commands").mkdir(parents=True)
        (tmp_path / ".claude" / "commands" / "cost.md").write_text("---\n---\nX\n")
        action = classify_input("/cost", workdir=tmp_path)
        assert isinstance(action, LocalCommand)


class TestHelpers:
    def test_skill_trigger_prompt_shape(self):
        p = skill_trigger_prompt("deep-research", "query here")
        assert "Skill" in p
        assert "skill='deep-research'" in p
        assert "query here" in p

    def test_expand_command_file_strips_frontmatter_and_substitutes(self, tmp_path: Path):
        f = tmp_path / "c.md"
        f.write_text("---\ndescription: d\n---\nHello $ARGUMENTS!\n")
        assert expand_command_file(f, "world") == "Hello world!"
