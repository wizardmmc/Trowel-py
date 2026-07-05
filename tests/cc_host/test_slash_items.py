"""Tests for cc_host.slash_items — scans ~/.claude/{skills,commands} + the
workdir's .claude/{skills,commands} for the '/' autocomplete, plus a hardcoded
bundled skill map (slice-027 C1).

cc init's slash_commands/skills/agents are bare names (z.array(z.string()));
description is NOT available there. This loader reads frontmatter locally so the
picker can show name + description like cc's own commandSuggestions.ts.
"""
from pathlib import Path

import pytest

from trowel_py.cc_host.slash_items import (
    BUNDLED_SKILLS,
    SlashItem,
    list_slash_items,
)


def _write_skill_md(skill_dir: Path, name: str, description: str) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody\n",
        encoding="utf-8",
    )


def _write_command_md(cmd_dir: Path, name: str, description: str) -> None:
    cmd_dir.mkdir(parents=True, exist_ok=True)
    (cmd_dir / f"{name}.md").write_text(
        f"---\ndescription: {description}\n---\nbody $ARGUMENTS\n",
        encoding="utf-8",
    )


class TestListSlashItems:
    """Priority: project > user > bundled (project overrides same-named user
    overrides same-named bundled). Mirrors cc's settings source precedence."""

    def test_scans_user_skill_dir(self, tmp_path: Path):
        user_skills = tmp_path / "user" / "skills"
        _write_skill_md(user_skills / "monthly-etf", "monthly-etf", "月度ETF推荐")
        out = list_slash_items("/wd", user_skills_dir=user_skills)
        item = next(i for i in out if i.name == "monthly-etf")
        assert isinstance(item, SlashItem)
        assert item.description == "月度ETF推荐"
        assert item.type == "skill"
        assert item.source == "user"

    def test_scans_project_command_dir(self, tmp_path: Path):
        proj_cmds = tmp_path / "proj" / ".claude" / "commands"
        _write_command_md(proj_cmds, "deploy", "部署到生产")
        out = list_slash_items(
            str(tmp_path / "proj"), project_commands_dir=proj_cmds,
            user_skills_dir=tmp_path / "x1",  # avoid hitting real ~/.claude
            user_commands_dir=tmp_path / "x2",
            project_skills_dir=tmp_path / "x3",
        )
        item = next(i for i in out if i.name == "deploy")
        assert item.description == "部署到生产"
        assert item.type == "command"
        assert item.source == "project"

    def test_bundled_skills_always_present_as_floor(self, tmp_path: Path):
        """Even with empty dirs, the hardcoded bundled map is returned so the
        picker shows cc's built-in skills (verify / simplify / ...)."""
        out = list_slash_items(
            "/wd",
            user_skills_dir=tmp_path / "e1",
            user_commands_dir=tmp_path / "e2",
            project_skills_dir=tmp_path / "e3",
            project_commands_dir=tmp_path / "e4",
        )
        bundled = {i.name: i for i in out if i.source == "bundled"}
        assert "verify" in bundled
        assert "simplify" in bundled
        assert bundled["verify"].type == "skill"
        assert bundled["verify"].description  # non-empty

    def test_builtin_commands_always_present(self, tmp_path: Path):
        """slice-027: trowel's own /model /effort /cost /status (handled in
        input.py, not on disk) MUST appear so the user can discover them —
        without this they'd be invisible in the '/' autocomplete."""
        out = list_slash_items(
            "/wd",
            user_skills_dir=tmp_path / "e1",
            user_commands_dir=tmp_path / "e2",
            project_skills_dir=tmp_path / "e3",
            project_commands_dir=tmp_path / "e4",
        )
        names = {i.name: i for i in out if i.source == "builtin"}
        assert {"model", "effort", "cost", "status"} <= set(names)
        assert names["model"].type == "command"
        assert names["model"].description  # non-empty

    def test_project_overrides_user_same_name(self, tmp_path: Path):
        user_skills = tmp_path / "user" / "skills"
        proj_skills = tmp_path / "proj" / ".claude" / "skills"
        _write_skill_md(user_skills / "shared", "shared", "user copy")
        _write_skill_md(proj_skills / "shared", "shared", "project copy")
        out = list_slash_items(
            str(tmp_path / "proj"),
            user_skills_dir=user_skills,
            project_skills_dir=proj_skills,
            user_commands_dir=tmp_path / "e1",
            project_commands_dir=tmp_path / "e2",
        )
        shared = [i for i in out if i.name == "shared"]
        assert len(shared) == 1  # deduped
        assert shared[0].source == "project"
        assert shared[0].description == "project copy"

    def test_user_overrides_bundled_same_name(self, tmp_path: Path):
        """A user skill named 'verify' shadows the bundled one."""
        user_skills = tmp_path / "user" / "skills"
        _write_skill_md(user_skills / "verify", "verify", "my custom verify")
        out = list_slash_items(
            "/wd",
            user_skills_dir=user_skills,
            user_commands_dir=tmp_path / "e1",
            project_skills_dir=tmp_path / "e2",
            project_commands_dir=tmp_path / "e3",
        )
        v = [i for i in out if i.name == "verify"]
        assert len(v) == 1
        assert v[0].source == "user"
        assert v[0].description == "my custom verify"

    def test_missing_dirs_do_not_crash(self, tmp_path: Path):
        """Nonexistent dirs are treated as empty (no exception)."""
        out = list_slash_items(
            "/no/such/workdir",
            user_skills_dir=tmp_path / "missing",
            user_commands_dir=tmp_path / "missing2",
            project_skills_dir=tmp_path / "missing3",
            project_commands_dir=tmp_path / "missing4",
        )
        assert any(i.source == "bundled" for i in out)

    def test_skill_md_without_frontmatter_falls_back_to_dir_name(
        self, tmp_path: Path
    ):
        """A SKILL.md with no frontmatter — name falls back to the parent dir."""
        d = tmp_path / "user" / "skills" / "nofront"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("just body, no frontmatter", encoding="utf-8")
        out = list_slash_items(
            "/wd",
            user_skills_dir=tmp_path / "user" / "skills",
            user_commands_dir=tmp_path / "e1",
            project_skills_dir=tmp_path / "e2",
            project_commands_dir=tmp_path / "e3",
        )
        names = [i.name for i in out if i.source == "user"]
        assert "nofront" in names

    def test_command_md_without_frontmatter_still_listed(self, tmp_path: Path):
        """A command .md with no frontmatter still shows (empty description)."""
        cmds = tmp_path / "proj" / ".claude" / "commands"
        cmds.mkdir(parents=True)
        (cmds / "bare.md").write_text("no frontmatter here", encoding="utf-8")
        out = list_slash_items(
            str(tmp_path / "proj"),
            project_commands_dir=cmds,
            user_skills_dir=tmp_path / "e1",
            user_commands_dir=tmp_path / "e2",
            project_skills_dir=tmp_path / "e3",
        )
        bare = next(i for i in out if i.name == "bare")
        assert bare.type == "command"
        assert bare.source == "project"

    def test_description_inner_quotes_preserved(self, tmp_path: Path):
        """slice-027: frontmatter description with inner apostrophes stays
        intact — we strip only ONE matched surrounding quote pair, not inner."""
        user_skills = tmp_path / "user" / "skills"
        _write_skill_md(user_skills / "dontstop", "dontstop", "don't stop me")
        out = list_slash_items(
            "/wd",
            user_skills_dir=user_skills,
            user_commands_dir=tmp_path / "e1",
            project_skills_dir=tmp_path / "e2",
            project_commands_dir=tmp_path / "e3",
        )
        item = next(i for i in out if i.name == "dontstop")
        assert item.description == "don't stop me"

    def test_unreadable_skill_md_skipped_not_raised(self, tmp_path: Path):
        """A skill md that raises OSError on read is skipped, not crashing the
        whole /cc/slash-items response."""
        user_skills = tmp_path / "user" / "skills"
        bad = user_skills / "broken"
        bad.mkdir(parents=True)
        (bad / "SKILL.md").write_text("ok", encoding="utf-8")
        # make SKILL.md unreadable (chmod 000); skip test if running as root
        import os
        if os.geteuid() == 0:
            pytest.skip("chmod doesn't deny root")
        (bad / "SKILL.md").chmod(0o000)
        try:
            # also have a good one so we can assert the listing still returns
            _write_skill_md(user_skills / "good", "good", "fine")
            out = list_slash_items(
                "/wd",
                user_skills_dir=user_skills,
                user_commands_dir=tmp_path / "e1",
                project_skills_dir=tmp_path / "e2",
                project_commands_dir=tmp_path / "e3",
            )
            names = [i.name for i in out if i.source == "user"]
            assert "good" in names
            assert "broken" not in names
        finally:
            (bad / "SKILL.md").chmod(0o644)


class TestBundledMap:
    """The hardcoded map of cc's built-in skills (extracted from the leaked
    cc source — may lag the installed version; user/project skills always
    override). Sourced from src/skills/bundled/ initBundledSkills."""

    def test_map_is_non_empty(self):
        assert len(BUNDLED_SKILLS) >= 10

    def test_keys_are_hyphenated_lowercase(self):
        """cc skill names use hyphens (update-config), never underscores."""
        for k in BUNDLED_SKILLS:
            assert "_" not in k, f"{k!r} should use hyphens not underscores"
            assert k == k.lower(), f"{k!r} should be lowercase"

    def test_all_values_are_non_empty_strings(self):
        for k, v in BUNDLED_SKILLS.items():
            assert isinstance(v, str) and v.strip(), f"{k!r} has empty desc"
