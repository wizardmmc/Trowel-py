"""Tests for cc_host.slash_items — scans ~/.claude/{skills,commands} + the
workdir's .claude/{skills,commands} for the '/' autocomplete, plus a hardcoded
bundled skill map (slice-027 C1).

cc init's slash_commands/skills/agents are bare names (z.array(z.string()));
description is NOT available there. This loader reads frontmatter locally so the
picker can show name + description like cc's own commandSuggestions.ts.
"""
from pathlib import Path
import json

import pytest

from trowel_py.cc_host.slash_items import (
    BUNDLED_SKILLS,
    SlashItem,
    _parse_frontmatter,
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

    def test_map_covers_cc_builtins_seen_in_init(self):
        """slice-042 P3: cc 2.1.197 built-in skills (no disk file, surface in
        the init roster) have Chinese descriptions so the picker is readable."""
        for name in (
            "deep-research", "code-review", "run", "init",
            "review", "security-review",
        ):
            assert name in BUNDLED_SKILLS, f"{name!r} missing from BUNDLED_SKILLS"


class TestParseFrontmatterBlockScalar:
    """slice-042 P3: multi-line description (| / > block scalar) must be parsed,
    not skipped (C-6) — else plugin/builtin skills with long descriptions show
    up description-less."""

    def test_literal_block_pipe(self):
        fm = _parse_frontmatter(
            "---\nname: x\ndescription: |\n  line one\n  line two\n---\n"
        )
        assert fm["description"] == "line one line two"

    def test_folded_block_greater(self):
        fm = _parse_frontmatter(
            "---\nname: x\ndescription: >\n  alpha\n  beta\n---\n"
        )
        assert fm["description"] == "alpha beta"

    def test_block_with_strip_chomping(self):
        fm = _parse_frontmatter(
            "---\nname: x\ndescription: |-\n  a\n  b\n---\n"
        )
        assert fm["description"] == "a b"

    def test_block_blank_line_is_paragraph_break(self):
        """Blank lines are paragraph breaks inside a block scalar — must NOT
        terminate the block (HIGH fix: old parser cut at the first blank line
        and lost the rest)."""
        fm = _parse_frontmatter(
            "---\nname: x\ndescription: |\n  first paragraph\n\n  second paragraph\n---\n"
        )
        assert fm["description"] == "first paragraph second paragraph"

    def test_single_line_still_works(self):
        fm = _parse_frontmatter("---\nname: x\ndescription: short one-liner\n---\n")
        assert fm["description"] == "short one-liner"

    def test_inner_quotes_preserved(self):
        fm = _parse_frontmatter("---\nname: x\ndescription: \"don't stop\"\n---\n")
        assert fm["description"] == "don't stop"

    def test_no_frontmatter_returns_empty(self):
        assert _parse_frontmatter("just body, no frontmatter") == {}


class TestRealInitFixture:
    """slice-042 P5: the recorded real cc init roster (cc 2.1.197, 291 items)
    must be fully covered after merge — this is C-1 (the 'keep up with cc
    updates' floor). Fixture from the M0 spike; isolated dirs so it doesn't
    depend on the host's ~/.claude."""

    _FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "cc-init-291.json"

    def test_real_roster_fully_covered(self, tmp_path):
        if not self._FIXTURE.exists():
            pytest.skip("cc-init-291.json fixture not present")
        from trowel_py.cc_host.slash_items import _CC_TUI_COMMANDS

        roster = json.loads(self._FIXTURE.read_text(encoding="utf-8"))["slash_commands"]
        out = list_slash_items(
            "/wd",
            user_skills_dir=tmp_path / "u1",
            user_commands_dir=tmp_path / "u2",
            project_skills_dir=tmp_path / "p1",
            project_commands_dir=tmp_path / "p2",
            plugins_dir=tmp_path / "plugins",  # isolated — plugin names come via floor
            init_roster=roster,
        )
        names = {i.name for i in out}

        def is_filtered(n: str) -> bool:
            return n.startswith("mcp__") or n in _CC_TUI_COMMANDS

        # C-1: every REAL slash item in the roster appears in the output.
        missing = {n for n in roster if n not in names and not is_filtered(n)}
        assert not missing, f"{len(missing)} real roster names missing: {sorted(missing)[:8]}"
        # And the filtered non-slash items (mcp__*, TUI) are indeed absent.
        leaked = {n for n in roster if n in names and is_filtered(n)}
        assert not leaked, f"filtered items leaked into output: {sorted(leaked)}"

    def test_real_roster_plugin_items_tagged_plugin(self, tmp_path):
        """Plugin skills (mp:name) in the real roster get source=plugin."""
        if not self._FIXTURE.exists():
            pytest.skip("cc-init-291.json fixture not present")
        roster = json.loads(self._FIXTURE.read_text(encoding="utf-8"))["slash_commands"]
        out = list_slash_items(
            "/wd",
            user_skills_dir=tmp_path / "u1",
            user_commands_dir=tmp_path / "u2",
            project_skills_dir=tmp_path / "p1",
            project_commands_dir=tmp_path / "p2",
            plugins_dir=tmp_path / "plugins",
            init_roster=roster,
        )
        by_name = {i.name: i for i in out}
        ecc = next(n for n in roster if n.startswith("everything-claude-code:"))
        assert by_name[ecc].source == "plugin"


class TestInitRosterFloor:
    """slice-042 P1: cc init's slash_commands roster is the name floor.

    C-1: after merge, the returned name set ⊇ init_roster. cc updates add a
    skill → it shows without any trowel code change. cc init gives bare names
    (no description), so floor items that no other source covers have empty
    description but still appear.
    """

    _EMPTY = dict(  # avoid hitting the real ~/.claude on disk
        user_skills_dir="__e1__",
        user_commands_dir="__e2__",
        project_skills_dir="__e3__",
        project_commands_dir="__e4__",
        plugins_dir="__plugins__",
    )

    def test_init_roster_names_all_appear(self, tmp_path):
        """A skill only cc knows about (no disk file) still shows — this is
        the core 'keep up with cc updates' guarantee."""
        kw = {k: tmp_path / v for k, v in self._EMPTY.items()}
        out = list_slash_items(
            "/wd", init_roster=["deep-research", "code-review", "my-new-skill"],
            **kw,
        )
        names = {i.name for i in out}
        assert {"deep-research", "code-review", "my-new-skill"} <= names

    def test_init_roster_does_not_shadow_disk_description(self, tmp_path):
        """If disk has a skill with a real description, the floor (bare name,
        no description) must NOT overwrite it. Disk > floor on same name."""
        us = tmp_path / "user" / "skills"
        _write_skill_md(us / "shared", "shared", "磁盘上的真实描述")
        out = list_slash_items(
            "/wd",
            init_roster=["shared", "floor-only"],
            user_skills_dir=us,
            user_commands_dir=tmp_path / "e1",
            project_skills_dir=tmp_path / "e2",
            project_commands_dir=tmp_path / "e3",
        )
        shared = next(i for i in out if i.name == "shared")
        assert shared.description == "磁盘上的真实描述"  # disk wins
        floor_only = next(i for i in out if i.name == "floor-only")
        assert floor_only.description == ""  # floor has no description
        assert floor_only.source in {"bundled", "init"}  # cc-internal fallback

    def test_init_roster_plugin_prefix_tagged_plugin(self, tmp_path):
        """A plugin skill arrives as 'plugin:skill' in the init roster; P1
        tags it source=plugin even before P2 scans the plugin dir for its
        description."""
        kw = {k: tmp_path / v for k, v in self._EMPTY.items()}
        out = list_slash_items(
            "/wd",
            init_roster=["everything-claude-code:aside", "codex:rescue"],
            **kw,
        )
        aside = next(i for i in out if i.name == "everything-claude-code:aside")
        assert aside.source == "plugin"
        rescue = next(i for i in out if i.name == "codex:rescue")
        assert rescue.source == "plugin"

    def test_init_roster_empty_is_no_op(self, tmp_path):
        """Default empty roster behaves exactly like slice-027 (no regression)."""
        kw = {k: tmp_path / v for k, v in self._EMPTY.items()}
        without = list_slash_items("/wd", **kw)
        with_empty = list_slash_items("/wd", init_roster=[], **kw)
        assert [i.name for i in without] == [i.name for i in with_empty]

    def test_init_roster_does_not_duplicate_disk_names(self, tmp_path):
        """A name present both on disk and in the roster appears once."""
        us = tmp_path / "user" / "skills"
        _write_skill_md(us / "monthly-etf", "monthly-etf", "月度ETF")
        out = list_slash_items(
            "/wd",
            init_roster=["monthly-etf", "deep-research"],
            user_skills_dir=us,
            user_commands_dir=tmp_path / "e1",
            project_skills_dir=tmp_path / "e2",
            project_commands_dir=tmp_path / "e3",
        )
        etfs = [i for i in out if i.name == "monthly-etf"]
        assert len(etfs) == 1

    def test_mcp_tool_help_filtered_from_floor(self, tmp_path):
        """MCP tool help (mcp__*) leaks into cc's roster but isn't a slash
        command — must not surface in the picker (selecting it errors)."""
        kw = {k: tmp_path / v for k, v in self._EMPTY.items()}
        out = list_slash_items(
            "/wd",
            init_roster=["mcp__plugin_ecc_exa__web_search_help", "deep-research"],
            **kw,
        )
        names = {i.name for i in out}
        assert "mcp__plugin_ecc_exa__web_search_help" not in names
        assert "deep-research" in names

    def test_cc_tui_commands_filtered_from_floor(self, tmp_path):
        """cc's built-in TUI/debug commands appear in the roster but headless
        tcc can't run them — filtered out; real skills still show."""
        kw = {k: tmp_path / v for k, v in self._EMPTY.items()}
        out = list_slash_items(
            "/wd",
            init_roster=["clear", "compact", "config", "context", "goal",
                         "deep-research"],
            **kw,
        )
        names = {i.name for i in out}
        for tui in ("clear", "compact", "config", "context", "goal"):
            assert tui not in names
        assert "deep-research" in names


class TestPluginScan:
    """slice-042 P2: scan ~/.claude/plugins/marketplaces/*/skills/ for plugin
    skill descriptions. Plugin skills keep their 'mp:skill' full name (C-4) and
    get source=plugin — this is what makes everything-claude-code etc. show up.
    """

    def _mp_skill(self, plugins: Path, mp: str, skill: str, desc: str) -> None:
        _write_skill_md(plugins / "marketplaces" / mp / "skills" / skill, skill, desc)

    def test_scan_marketplace_skill_description(self, tmp_path):
        plugins = tmp_path / "plugins"
        self._mp_skill(plugins, "everything-claude-code", "aside", "Quick aside note")
        out = list_slash_items(
            "/wd",
            plugins_dir=plugins,
            user_skills_dir=tmp_path / "e1",
            user_commands_dir=tmp_path / "e2",
            project_skills_dir=tmp_path / "e3",
            project_commands_dir=tmp_path / "e4",
            init_roster=["everything-claude-code:aside"],
        )
        item = next(i for i in out if i.name == "everything-claude-code:aside")
        assert item.source == "plugin"
        assert item.type == "skill"
        assert item.description == "Quick aside note"

    def test_plugin_found_without_init_roster(self, tmp_path):
        """Disk scan discovers plugin skills independently of the init roster —
        so a plugin skill shows even before cc sends init (e.g. session list
        loaded before the first turn)."""
        plugins = tmp_path / "plugins"
        self._mp_skill(plugins, "ecc", "aside", "note")
        out = list_slash_items(
            "/wd",
            plugins_dir=plugins,
            user_skills_dir=tmp_path / "e1",
            user_commands_dir=tmp_path / "e2",
            project_skills_dir=tmp_path / "e3",
            project_commands_dir=tmp_path / "e4",
        )
        assert any(
            i.name == "ecc:aside" and i.source == "plugin" for i in out
        )

    def test_plugin_description_beats_init_floor_empty(self, tmp_path):
        """P2's scanned description must win over the init floor's empty
        description for the same plugin skill (plugin scan > floor)."""
        plugins = tmp_path / "plugins"
        self._mp_skill(plugins, "ecc", "aside", "real description from SKILL.md")
        out = list_slash_items(
            "/wd",
            plugins_dir=plugins,
            user_skills_dir=tmp_path / "e1",
            user_commands_dir=tmp_path / "e2",
            project_skills_dir=tmp_path / "e3",
            project_commands_dir=tmp_path / "e4",
            init_roster=["ecc:aside"],
        )
        item = next(i for i in out if i.name == "ecc:aside")
        assert item.description == "real description from SKILL.md"

    def test_scan_marketplace_command_with_description(self, tmp_path):
        """slice-042 P2 fix: plugin commands (commands/*.md) are slash items too
        — ecc's code-review is a command, not a skill. Scan their frontmatter."""
        plugins = tmp_path / "plugins"
        cmds = plugins / "marketplaces" / "ecc" / "commands"
        cmds.mkdir(parents=True)
        (cmds / "code-review.md").write_text(
            "---\ndescription: Review the diff for bugs.\n---\n# Code Review\n",
            encoding="utf-8",
        )
        out = list_slash_items(
            "/wd",
            plugins_dir=plugins,
            user_skills_dir=tmp_path / "e1",
            user_commands_dir=tmp_path / "e2",
            project_skills_dir=tmp_path / "e3",
            project_commands_dir=tmp_path / "e4",
            init_roster=["ecc:code-review"],
        )
        item = next(i for i in out if i.name == "ecc:code-review")
        assert item.source == "plugin"
        assert item.description == "Review the diff for bugs."

    def test_plugin_command_without_frontmatter_empty_desc(self, tmp_path):
        """A plugin command .md with no frontmatter still shows (name from the
        init floor) but with empty description — ecc's real code-review.md is
        like this. Not a tcc bug; the plugin didn't ship a description."""
        plugins = tmp_path / "plugins"
        cmds = plugins / "marketplaces" / "ecc" / "commands"
        cmds.mkdir(parents=True)
        (cmds / "bare.md").write_text("# Bare\nNo frontmatter.\n", encoding="utf-8")
        out = list_slash_items(
            "/wd",
            plugins_dir=plugins,
            user_skills_dir=tmp_path / "e1",
            user_commands_dir=tmp_path / "e2",
            project_skills_dir=tmp_path / "e3",
            project_commands_dir=tmp_path / "e4",
            init_roster=["ecc:bare"],
        )
        item = next(i for i in out if i.name == "ecc:bare")
        assert item.source == "plugin"
        assert item.description == ""

    def test_missing_plugins_dir_no_crash(self, tmp_path):
        out = list_slash_items(
            "/wd",
            plugins_dir=tmp_path / "no-such-plugins",
            user_skills_dir=tmp_path / "e1",
            user_commands_dir=tmp_path / "e2",
            project_skills_dir=tmp_path / "e3",
            project_commands_dir=tmp_path / "e4",
        )
        assert any(i.source == "bundled" for i in out)
