"""Scan ~/.claude/{skills,commands} + <workdir>/.claude/{skills,commands} to
build the '/' autocomplete list (slice-027 C1).

cc init's slash_commands / skills / agents are bare name lists — no description
(coreSchemas.ts z.array(z.string())). This loader reads SKILL.md / command .md
frontmatter locally so the picker shows name + description like cc's own
commandSuggestions.ts. A hardcoded BUNDLED_SKILLS map covers cc's built-in
skills (whose source isn't on disk). Priority: project > user > bundled.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# cc's built-in skills, extracted from the leaked cc source
# (src/skills/bundled/ initBundledSkills). This may lag the installed version —
# user / project skills always override same-named bundled ones, and the picker
# surfaces cc init's full name roster too (so a missing-from-map bundled skill
# still appears, just description-less). Values are short trowel-side subtitles.
BUNDLED_SKILLS: dict[str, str] = {
    "update-config": "配置 cc harness (settings.json)：自动行为 / hooks / 权限 / 环境变量",
    "keybindings": "自定义键盘快捷键，改 ~/.claude/keybindings.json",
    "verify": "验证改动（编译 / 测试 / lint 全跑）",
    "debug": "调试卡死 / 慢会话",
    "lorem-ipsum": "生成长上下文测试填充文本",
    "skillify": "把当前 session 沉淀成 skill",
    "remember": "review auto-memory，提议晋升到 CLAUDE.md",
    "simplify": "review 改动代码，做复用 / 质量 / 效率清理",
    "batch": "大规模改动研究 + 规划，5–30 个 worktree agent 并行各开 PR",
    "stuck": "排查卡死 / 慢会话并上报",
    "claude-api": "用 Claude API / Anthropic SDK 构建应用",
    "loop": "周期性跑 prompt / slash 命令",
    "schedule-remote-agents": "创建 / 管理 cron 远程 agent",
    "claude-in-chrome": "自动化 Chrome 浏览器",
}

# trowel's own built-in slash commands — handled in input.py (NOT skills, NOT
# on disk). These must be listed here so the '/' autocomplete surfaces them;
# without this map the user has no way to discover /model, /effort, etc. The
# names line up with input.py's _LOCAL_COMMANDS (cost/status) +
# _RESTART_COMMANDS (effort/model). Other cc commands (/clear /help /compact
# /plan /config) are intentionally NOT listed — trowel doesn't implement them
# and the headless backend would reject them, so showing them would mislead.
BUILTIN_COMMANDS: dict[str, str] = {
    "model": "切换模型（回车弹出选择器，含别名 → 真实模型映射）",
    "effort": "切换 effort（回车弹出选择器：low/medium/high/max/auto/ultracode）",
    "cost": "显示当前会话累计花费",
    "status": "显示当前模型 / effort / 进程状态",
}


@dataclass(frozen=True)
class SlashItem:
    """One row of GET /cc/slash-items — the '/' autocomplete entry.

    Attributes:
        name: the slash name without leading '/', e.g. 'monthly-etf'.
        description: frontmatter description (empty if the md lacks one).
        source: where it came from — 'project' / 'user' / 'bundled'.
        type: 'skill' (from skills/) or 'command' (from commands/).
    """

    name: str
    description: str
    source: str
    type: str


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Parse a YAML-ish frontmatter block (--- ... ---) into a flat KV dict.

    Only handles single-line scalar keys (name / description); list values
    (allowed-tools etc.) and nested keys are skipped — we only need name +
    description here. Returns {} when the file has no frontmatter.
    """
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    block = parts[1]
    out: dict[str, str] = {}
    for line in block.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("- "):
            continue
        if ":" not in s:
            continue
        k, _, v = s.partition(":")
        v = v.strip()
        # Strip ONE matched pair of surrounding quotes; don't strip inner
        # quotes (so "don't stop" stays, not "dont stop").
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def _scan_skills(root: Path) -> dict[str, str]:
    """Scan a skills dir: each subdir is one skill, read its SKILL.md frontmatter.

    Returns name -> description. The name comes from frontmatter 'name' if
    present, else falls back to the subdir name. Missing root -> {}.
    """
    if not root.is_dir():
        return {}
    out: dict[str, str] = {}
    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue
        skill_md = sub / "SKILL.md"
        if not skill_md.is_file():
            continue
        try:
            fm = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        except OSError:
            continue  # unreadable skill md — skip, don't break the whole listing
        name = fm.get("name") or sub.name
        out[name] = fm.get("description", "")
    return out


def _scan_commands(root: Path) -> dict[str, str]:
    """Scan a commands dir: each *.md is one command, read its frontmatter.

    Returns stem -> description (stem is the filename without .md). Missing
    root -> {}.
    """
    if not root.is_dir():
        return {}
    out: dict[str, str] = {}
    for md in sorted(root.glob("*.md")):
        try:
            fm = _parse_frontmatter(md.read_text(encoding="utf-8"))
        except OSError:
            continue  # unreadable command md — skip
        out[md.stem] = fm.get("description", "")
    return out


def list_slash_items(
    workdir: str | Path,
    *,
    user_skills_dir: Path | None = None,
    user_commands_dir: Path | None = None,
    project_skills_dir: Path | None = None,
    project_commands_dir: Path | None = None,
) -> list[SlashItem]:
    """Build the '/' autocomplete list: project + user + bundled, deduped.

    Priority (first wins on name collision): project > user > bundled. This
    mirrors cc's settings source precedence — a project skill shadows a
    same-named user skill shadows a bundled one.

    Args:
        workdir: the session's working directory (project .claude/ lives here).
        user_skills_dir / user_commands_dir: override ~/.claude/{skills,commands}.
        project_skills_dir / project_commands_dir: override <workdir>/.claude/{...}.

    Returns:
        SlashItems sorted by (type, name) so the frontend can group skills vs
        commands and sort within each group without re-sorting.
    """
    wd = Path(workdir)
    us = user_skills_dir or (Path.home() / ".claude" / "skills")
    uc = user_commands_dir or (Path.home() / ".claude" / "commands")
    ps = project_skills_dir or (wd / ".claude" / "skills")
    pc = project_commands_dir or (wd / ".claude" / "commands")

    seen: dict[str, SlashItem] = {}

    # project first (highest priority), then user, then bundled (floor).
    layers: list[tuple[str, str, dict[str, str]]] = [
        ("project", "skill", _scan_skills(ps)),
        ("project", "command", _scan_commands(pc)),
        ("user", "skill", _scan_skills(us)),
        ("user", "command", _scan_commands(uc)),
    ]
    for source, typ, scan in layers:
        for name, desc in scan.items():
            if name in seen:
                continue
            seen[name] = SlashItem(
                name=name, description=desc, source=source, type=typ,
            )

    for name, desc in BUNDLED_SKILLS.items():
        if name in seen:
            continue
        seen[name] = SlashItem(
            name=name, description=desc, source="bundled", type="skill",
        )

    for name, desc in BUILTIN_COMMANDS.items():
        if name in seen:
            continue
        seen[name] = SlashItem(
            name=name, description=desc, source="builtin", type="command",
        )

    return sorted(seen.values(), key=lambda i: (i.type, i.name))
