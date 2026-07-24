from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trowel_py.cc_host.slash_items import SlashItem, list_slash_items


@dataclass(frozen=True)
class SlashSandbox:
    root: Path

    @property
    def workdir(self) -> Path:
        return self.root / "project"

    @property
    def user_skills(self) -> Path:
        return self.root / "user" / "skills"

    @property
    def user_commands(self) -> Path:
        return self.root / "user" / "commands"

    @property
    def project_skills(self) -> Path:
        return self.workdir / ".claude" / "skills"

    @property
    def project_commands(self) -> Path:
        return self.workdir / ".claude" / "commands"

    @property
    def plugins(self) -> Path:
        return self.root / "plugins"

    def list(self, **overrides: Any) -> list[SlashItem]:
        workdir = overrides.pop("workdir", self.workdir)
        options: dict[str, Any] = {
            "user_skills_dir": self.user_skills,
            "user_commands_dir": self.user_commands,
            "project_skills_dir": self.project_skills,
            "project_commands_dir": self.project_commands,
            "plugins_dir": self.plugins,
        }
        options.update(overrides)
        return list_slash_items(workdir, **options)


def write_skill(
    root: Path,
    name: str,
    description: str,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody\n",
        encoding="utf-8",
    )


def write_command(
    root: Path,
    name: str,
    description: str,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.md").write_text(
        f"---\ndescription: {description}\n---\nbody $ARGUMENTS\n",
        encoding="utf-8",
    )


def write_marketplace_skill(
    plugins: Path,
    marketplace: str,
    skill: str,
    description: str,
) -> None:
    write_skill(
        plugins / "marketplaces" / marketplace / "skills" / skill,
        skill,
        description,
    )
