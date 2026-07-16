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
    # slice-042 P3: cc 2.1.197 built-in skills seen in the init roster but
    # with no disk file — Chinese descriptions so the picker reads naturally.
    "deep-research": "多源深研报告（fan-out 搜索 + 对抗验证 + 引用综合）",
    "code-review": "review 当前 diff 的正确性 bug 与复用/简化/效率清理",
    "review": "review 当前 diff",
    "security-review": "安全审查当前 diff",
    "run": "启动并驱动本项目 app，看改动效果",
    "init": "初始化项目的 CLAUDE.md",
    "fewer-permission-prompts": "扫描 transcript，把常用只读命令加进 allowlist 减少权限弹窗",
    "keybindings-help": "自定义键盘快捷键（改 ~/.claude/keybindings.json）",
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

# slice-042: cc's built-in TUI / debug slash commands that leak into the init
# roster but headless tcc can't run — selecting one sends a command cc's
# headless backend rejects, so we filter them out of the floor. (Inverse of
# BUILTIN_COMMANDS above: those are tcc's OWN commands it does implement.)
# cc may add more over versions — extend this set when spotted.
_CC_TUI_COMMANDS: frozenset[str] = frozenset({
    "clear", "compact", "config", "context", "heapdump", "reload-skills",
    "usage", "insights", "goal",
})


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

    Handles single-line scalars AND block scalars (|, >, |-, >-, etc.) for
    multi-line descriptions — folds the indented body into one space-joined
    string (C-6, slice-042 P3: the old parser skipped these and lost the
    description). List values (allowed-tools) and nested keys are skipped —
    we only need name + description. Returns {} with no frontmatter.
    """
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    lines = parts[1].splitlines()
    out: dict[str, str] = {}
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not s or s.startswith("#") or s.startswith("- "):
            i += 1
            continue
        if ":" not in s:
            i += 1
            continue
        k, _, v = s.partition(":")
        k = k.strip()
        v = v.strip()
        if v in ("|", ">", "|-", "|+", ">-", ">+"):
            # Block scalar: collect the following indented lines, fold with
            # spaces. Blank lines are legitimate paragraph breaks inside the
            # block (don't terminate); a dedented non-blank line is the next
            # key and ends the block.
            block: list[str] = []
            i += 1
            while i < len(lines):
                if not lines[i].strip():
                    i += 1  # blank line — paragraph break, stays in block
                    continue
                if not (lines[i].startswith(" ") or lines[i].startswith("\t")):
                    break  # dedented non-blank line — next key, block ends
                block.append(lines[i].strip())
                i += 1
            out[k] = " ".join(block)
            continue
        # Strip ONE matched pair of surrounding quotes; don't strip inner
        # quotes (so "don't stop" stays, not "dont stop").
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k] = v
        i += 1
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


def _scan_plugins(plugins_root: Path) -> dict[str, str]:
    """Scan ~/.claude/plugins/marketplaces/<mp>/{skills,commands}/.

    Plugin slash items live in two shapes: skills/<skill>/SKILL.md and
    commands/<cmd>.md (slice-042 P2 fix: commands are slash items too — ecc's
    code-review is a command, not a skill). Returns '{mp}:{name}' -> desc;
    a skill wins over a same-named command. cache/ holds versioned copies
    deduped against marketplaces (marketplaces wins, not scanned). agents/ are
    @-triggered, not in the slash roster, so not scanned. Missing root -> {}.
    """
    mp_root = plugins_root / "marketplaces"
    if not mp_root.is_dir():
        return {}
    out: dict[str, str] = {}
    for mp in sorted(mp_root.iterdir()):
        if not mp.is_dir():
            continue
        skills_dir = mp / "skills"
        if skills_dir.is_dir():
            for sub in sorted(skills_dir.iterdir()):
                if not sub.is_dir():
                    continue
                skill_md = sub / "SKILL.md"
                if not skill_md.is_file():
                    continue
                try:
                    fm = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
                except OSError:
                    continue  # unreadable plugin skill md — skip
                out[f"{mp.name}:{sub.name}"] = fm.get("description", "")
        cmds_dir = mp / "commands"
        if cmds_dir.is_dir():
            for md in sorted(cmds_dir.glob("*.md")):
                try:
                    fm = _parse_frontmatter(md.read_text(encoding="utf-8"))
                except OSError:
                    continue  # unreadable plugin command md — skip
                key = f"{mp.name}:{md.stem}"
                if key not in out:  # a same-named skill already won
                    out[key] = fm.get("description", "")
    return out


def list_slash_items(
    workdir: str | Path,
    *,
    user_skills_dir: Path | None = None,
    user_commands_dir: Path | None = None,
    project_skills_dir: Path | None = None,
    project_commands_dir: Path | None = None,
    plugins_dir: Path | None = None,
    init_roster: list[str] | None = None,
) -> list[SlashItem]:
    """Build the '/' autocomplete list, deduped.

    Layers (first wins on name collision): project > user > plugin > bundled
    > builtin > cc init roster (floor). The init roster is the part that keeps
    up with cc updates — a skill cc adds shows with no trowel code change.

    Args:
        workdir: the session's working directory (project .claude/ lives here).
        user_skills_dir / user_commands_dir: override ~/.claude/{skills,commands}.
        project_skills_dir / project_commands_dir: override <workdir>/.claude/{...}.
        plugins_dir: override ~/.claude/plugins (marketplace skills + commands).
        init_roster: cc init's slash_commands bare-name list — the name floor.

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
    pp = plugins_dir or (Path.home() / ".claude" / "plugins")
    layers: list[tuple[str, str, dict[str, str]]] = [
        ("project", "skill", _scan_skills(ps)),
        ("project", "command", _scan_commands(pc)),
        ("user", "skill", _scan_skills(us)),
        ("user", "command", _scan_commands(uc)),
        ("plugin", "skill", _scan_plugins(pp)),
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

    # slice-042 P1: cc init's slash_commands roster is the authoritative name
    # floor — the one source that keeps up with cc updates (a skill cc adds
    # lands here, shows with no trowel code change). init gives bare names with
    # no description, so a name already covered by disk/bundled/builtin keeps
    # its richer entry (floor never overwrites — disk > floor on collisions).
    # Plugin skills arrive as "plugin:skill" → source=plugin (P2 scans the
    # plugin dir to fill the description); bare names are cc-internal →
    # source=bundled (P3's BUNDLED_SKILLS table fills the description).
    for name in init_roster or []:
        # Filter non-slash items cc leaks into the roster: MCP tool help
        # (mcp__*) and cc's TUI/debug commands (_CC_TUI_COMMANDS). These would
        # surface in the picker but either aren't slash commands or headless
        # cc rejects them.
        if name.startswith("mcp__") or name in _CC_TUI_COMMANDS:
            continue
        if name in seen:
            continue
        seen[name] = SlashItem(
            name=name,
            description="",
            source="plugin" if ":" in name else "bundled",
            type="skill",
        )

    return sorted(seen.values(), key=lambda i: (i.type, i.name))
