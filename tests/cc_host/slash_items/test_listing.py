from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.cc_host.slash_items.support import (
    SlashSandbox,
    write_command,
    write_skill,
)
from trowel_py.cc_host.slash_items import SlashItem


class TestListSlashItems:
    def test_scans_user_skill_dir(self, tmp_path: Path) -> None:
        sandbox = SlashSandbox(tmp_path)
        write_skill(
            sandbox.user_skills / "monthly-plan",
            "monthly-plan",
            "月度计划",
        )
        item = next(item for item in sandbox.list() if item.name == "monthly-plan")
        assert isinstance(item, SlashItem)
        assert item.description == "月度计划"
        assert item.type == "skill"
        assert item.source == "user"

    def test_scans_project_command_dir(self, tmp_path: Path) -> None:
        sandbox = SlashSandbox(tmp_path)
        write_command(sandbox.project_commands, "deploy", "部署到生产")
        item = next(item for item in sandbox.list() if item.name == "deploy")
        assert item.description == "部署到生产"
        assert item.type == "command"
        assert item.source == "project"

    def test_bundled_skills_always_present_as_floor(
        self,
        tmp_path: Path,
    ) -> None:
        bundled = {
            item.name: item
            for item in SlashSandbox(tmp_path).list()
            if item.source == "bundled"
        }
        assert "verify" in bundled
        assert "simplify" in bundled
        assert bundled["verify"].type == "skill"
        assert bundled["verify"].description

    def test_builtin_commands_always_present(self, tmp_path: Path) -> None:
        commands = {
            item.name: item
            for item in SlashSandbox(tmp_path).list()
            if item.source == "builtin"
        }
        assert {"model", "effort", "cost", "status"} <= set(commands)
        assert commands["model"].type == "command"
        assert commands["model"].description

    def test_project_overrides_user_same_name(self, tmp_path: Path) -> None:
        sandbox = SlashSandbox(tmp_path)
        write_skill(
            sandbox.user_skills / "shared",
            "shared",
            "user copy",
        )
        write_skill(
            sandbox.project_skills / "shared",
            "shared",
            "project copy",
        )
        shared = [item for item in sandbox.list() if item.name == "shared"]
        assert len(shared) == 1
        assert shared[0].source == "project"
        assert shared[0].description == "project copy"

    def test_user_overrides_bundled_same_name(self, tmp_path: Path) -> None:
        sandbox = SlashSandbox(tmp_path)
        write_skill(
            sandbox.user_skills / "verify",
            "verify",
            "custom verify",
        )
        verify = [item for item in sandbox.list() if item.name == "verify"]
        assert len(verify) == 1
        assert verify[0].source == "user"
        assert verify[0].description == "custom verify"

    def test_missing_dirs_do_not_crash(self, tmp_path: Path) -> None:
        items = SlashSandbox(tmp_path / "missing").list()
        assert any(item.source == "bundled" for item in items)

    def test_skill_md_without_frontmatter_falls_back_to_dir_name(
        self,
        tmp_path: Path,
    ) -> None:
        sandbox = SlashSandbox(tmp_path)
        skill_dir = sandbox.user_skills / "nofront"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "just body, no frontmatter",
            encoding="utf-8",
        )
        names = [item.name for item in sandbox.list() if item.source == "user"]
        assert "nofront" in names

    def test_command_md_without_frontmatter_still_listed(
        self,
        tmp_path: Path,
    ) -> None:
        sandbox = SlashSandbox(tmp_path)
        sandbox.project_commands.mkdir(parents=True)
        (sandbox.project_commands / "bare.md").write_text(
            "no frontmatter here",
            encoding="utf-8",
        )
        bare = next(item for item in sandbox.list() if item.name == "bare")
        assert bare.type == "command"
        assert bare.source == "project"

    def test_description_inner_quotes_preserved(
        self,
        tmp_path: Path,
    ) -> None:
        sandbox = SlashSandbox(tmp_path)
        write_skill(
            sandbox.user_skills / "dontstop",
            "dontstop",
            "don't stop me",
        )
        item = next(item for item in sandbox.list() if item.name == "dontstop")
        assert item.description == "don't stop me"

    def test_unreadable_skill_md_skipped_not_raised(
        self,
        tmp_path: Path,
    ) -> None:
        if os.geteuid() == 0:
            pytest.skip("chmod does not deny root")
        sandbox = SlashSandbox(tmp_path)
        bad = sandbox.user_skills / "broken"
        bad.mkdir(parents=True)
        skill_file = bad / "SKILL.md"
        skill_file.write_text("ok", encoding="utf-8")
        skill_file.chmod(0o000)
        try:
            write_skill(
                sandbox.user_skills / "good",
                "good",
                "fine",
            )
            names = {item.name for item in sandbox.list() if item.source == "user"}
            assert "good" in names
            assert "broken" not in names
        finally:
            skill_file.chmod(0o644)
