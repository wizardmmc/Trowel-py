from __future__ import annotations

from pathlib import Path

from tests.cc_host.slash_items.support import SlashSandbox, write_skill
from trowel_py.cc_host.slash_items import (
    BUNDLED_SKILLS,
    _CC_TUI_COMMANDS,
)

_ROSTER_SAMPLE = (
    *BUNDLED_SKILLS,
    "everything-claude-code:aside",
    "mcp__plugin_example__search_help",
    *_CC_TUI_COMMANDS,
)


class TestBundledMap:
    def test_map_is_non_empty(self) -> None:
        assert len(BUNDLED_SKILLS) >= 10

    def test_keys_are_hyphenated_lowercase(self) -> None:
        for name in BUNDLED_SKILLS:
            assert "_" not in name
            assert name == name.lower()

    def test_all_values_are_non_empty_strings(self) -> None:
        for description in BUNDLED_SKILLS.values():
            assert isinstance(description, str)
            assert description.strip()

    def test_map_covers_cc_builtins_seen_in_init(self) -> None:
        expected = (
            "deep-research",
            "code-review",
            "run",
            "init",
            "review",
            "security-review",
        )
        assert all(name in BUNDLED_SKILLS for name in expected)


class TestRepresentativeInitRoster:
    def test_representative_roster_fully_covered(
        self,
        tmp_path: Path,
    ) -> None:
        names = {
            item.name
            for item in SlashSandbox(tmp_path).list(init_roster=list(_ROSTER_SAMPLE))
        }
        filtered = {
            name
            for name in _ROSTER_SAMPLE
            if name.startswith("mcp__") or name in _CC_TUI_COMMANDS
        }
        assert set(_ROSTER_SAMPLE) - filtered <= names
        assert not names & filtered

    def test_representative_roster_plugin_items_tagged_plugin(
        self,
        tmp_path: Path,
    ) -> None:
        by_name = {
            item.name: item
            for item in SlashSandbox(tmp_path).list(init_roster=list(_ROSTER_SAMPLE))
        }
        plugin_name = next(
            name
            for name in _ROSTER_SAMPLE
            if name.startswith("everything-claude-code:")
        )
        assert by_name[plugin_name].source == "plugin"


class TestInitRosterFloor:
    def test_init_roster_names_all_appear(self, tmp_path: Path) -> None:
        items = SlashSandbox(tmp_path).list(
            init_roster=[
                "deep-research",
                "code-review",
                "my-new-skill",
            ]
        )
        names = {item.name for item in items}
        assert {
            "deep-research",
            "code-review",
            "my-new-skill",
        } <= names

    def test_init_roster_does_not_shadow_disk_description(
        self,
        tmp_path: Path,
    ) -> None:
        sandbox = SlashSandbox(tmp_path)
        write_skill(
            sandbox.user_skills / "shared",
            "shared",
            "磁盘描述",
        )
        by_name = {
            item.name: item
            for item in sandbox.list(init_roster=["shared", "floor-only"])
        }
        assert by_name["shared"].description == "磁盘描述"
        assert by_name["floor-only"].description == ""
        assert by_name["floor-only"].source in {"bundled", "init"}

    def test_init_roster_plugin_prefix_tagged_plugin(
        self,
        tmp_path: Path,
    ) -> None:
        items = SlashSandbox(tmp_path).list(
            init_roster=[
                "everything-claude-code:aside",
                "codex:rescue",
            ]
        )
        by_name = {item.name: item for item in items}
        assert by_name["everything-claude-code:aside"].source == "plugin"
        assert by_name["codex:rescue"].source == "plugin"

    def test_init_roster_empty_is_no_op(self, tmp_path: Path) -> None:
        sandbox = SlashSandbox(tmp_path)
        without = sandbox.list()
        with_empty = sandbox.list(init_roster=[])
        assert [item.name for item in without] == [item.name for item in with_empty]

    def test_init_roster_does_not_duplicate_disk_names(
        self,
        tmp_path: Path,
    ) -> None:
        sandbox = SlashSandbox(tmp_path)
        write_skill(
            sandbox.user_skills / "monthly-plan",
            "monthly-plan",
            "月度计划",
        )
        items = sandbox.list(init_roster=["monthly-plan", "deep-research"])
        assert sum(item.name == "monthly-plan" for item in items) == 1

    def test_mcp_tool_help_filtered_from_floor(
        self,
        tmp_path: Path,
    ) -> None:
        names = {
            item.name
            for item in SlashSandbox(tmp_path).list(
                init_roster=[
                    "mcp__plugin_example__search_help",
                    "deep-research",
                ]
            )
        }
        assert "mcp__plugin_example__search_help" not in names
        assert "deep-research" in names

    def test_cc_tui_commands_filtered_from_floor(
        self,
        tmp_path: Path,
    ) -> None:
        names = {
            item.name
            for item in SlashSandbox(tmp_path).list(
                init_roster=[
                    "clear",
                    "compact",
                    "config",
                    "context",
                    "goal",
                    "deep-research",
                ]
            )
        }
        assert not names & {"clear", "compact", "config", "context", "goal"}
        assert "deep-research" in names
