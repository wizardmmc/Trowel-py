from __future__ import annotations

from pathlib import Path

from tests.cc_host.slash_items.support import (
    SlashSandbox,
    write_marketplace_skill,
)


class TestPluginScan:
    def test_scan_marketplace_skill_description(
        self,
        tmp_path: Path,
    ) -> None:
        sandbox = SlashSandbox(tmp_path)
        write_marketplace_skill(
            sandbox.plugins,
            "example",
            "aside",
            "Quick aside note",
        )
        item = next(
            item
            for item in sandbox.list(init_roster=["example:aside"])
            if item.name == "example:aside"
        )
        assert item.source == "plugin"
        assert item.type == "skill"
        assert item.description == "Quick aside note"

    def test_plugin_found_without_init_roster(
        self,
        tmp_path: Path,
    ) -> None:
        sandbox = SlashSandbox(tmp_path)
        write_marketplace_skill(
            sandbox.plugins,
            "example",
            "aside",
            "note",
        )
        assert any(
            item.name == "example:aside" and item.source == "plugin"
            for item in sandbox.list()
        )

    def test_plugin_description_beats_init_floor_empty(
        self,
        tmp_path: Path,
    ) -> None:
        sandbox = SlashSandbox(tmp_path)
        write_marketplace_skill(
            sandbox.plugins,
            "example",
            "aside",
            "real description from SKILL.md",
        )
        item = next(
            item
            for item in sandbox.list(init_roster=["example:aside"])
            if item.name == "example:aside"
        )
        assert item.description == "real description from SKILL.md"

    def test_scan_marketplace_command_with_description(
        self,
        tmp_path: Path,
    ) -> None:
        sandbox = SlashSandbox(tmp_path)
        commands = sandbox.plugins / "marketplaces" / "example" / "commands"
        commands.mkdir(parents=True)
        (commands / "code-review.md").write_text(
            "---\ndescription: Review the diff for bugs.\n---\n",
            encoding="utf-8",
        )
        item = next(
            item
            for item in sandbox.list(init_roster=["example:code-review"])
            if item.name == "example:code-review"
        )
        assert item.source == "plugin"
        assert item.description == "Review the diff for bugs."

    def test_plugin_command_without_frontmatter_empty_desc(
        self,
        tmp_path: Path,
    ) -> None:
        sandbox = SlashSandbox(tmp_path)
        commands = sandbox.plugins / "marketplaces" / "example" / "commands"
        commands.mkdir(parents=True)
        (commands / "bare.md").write_text(
            "# Bare\nNo frontmatter.\n",
            encoding="utf-8",
        )
        item = next(
            item
            for item in sandbox.list(init_roster=["example:bare"])
            if item.name == "example:bare"
        )
        assert item.source == "plugin"
        assert item.description == ""

    def test_missing_plugins_dir_no_crash(self, tmp_path: Path) -> None:
        items = SlashSandbox(tmp_path).list(plugins_dir=tmp_path / "no-such-plugins")
        assert any(item.source == "bundled" for item in items)
