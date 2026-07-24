"""汇总 CC 的本地 skill、command、plugin 与内置 slash items。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from trowel_py.cc_host.frontmatter import (
    parse_frontmatter as _run_parse_frontmatter,
)

# 内置描述可能落后于已安装的 CC；init roster 负责补齐名称。
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
    "deep-research": "多源深研报告（fan-out 搜索 + 对抗验证 + 引用综合）",
    "code-review": "review 当前 diff 的正确性 bug 与复用/简化/效率清理",
    "review": "review 当前 diff",
    "security-review": "安全审查当前 diff",
    "run": "启动并驱动本项目 app，看改动效果",
    "init": "初始化项目的 CLAUDE.md",
    "fewer-permission-prompts": "扫描 transcript，把常用只读命令加进 allowlist 减少权限弹窗",
    "keybindings-help": "自定义键盘快捷键（改 ~/.claude/keybindings.json）",
}

# 这些命令由 input.py 实现，不存在于磁盘。
BUILTIN_COMMANDS: dict[str, str] = {
    "model": "切换模型（回车弹出选择器，含别名 → 真实模型映射）",
    "effort": "切换 effort（回车弹出选择器：low/medium/high/max/auto/ultracode）",
    "cost": "显示当前会话累计花费",
    "status": "显示当前模型 / effort / 进程状态",
}

# Headless CC 无法执行这些 TUI/debug 命令，不能把它们暴露到补全列表。
_CC_TUI_COMMANDS: frozenset[str] = frozenset({
    "clear", "compact", "config", "context", "heapdump", "reload-skills",
    "usage", "insights", "goal",
})


@dataclass(frozen=True)
class SlashItem:
    """`/cc/slash-items` 返回的一条补全项。"""

    name: str
    description: str
    source: str
    type: str


def _parse_frontmatter(text: str) -> dict[str, str]:
    return _run_parse_frontmatter(text)


def _scan_skills(root: Path) -> dict[str, str]:
    """读取 skill 子目录；缺少 name 时回退到目录名。"""
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
            continue
        name = fm.get("name") or sub.name
        out[name] = fm.get("description", "")
    return out


def _scan_commands(root: Path) -> dict[str, str]:
    """读取 command Markdown，以文件名作为命令名。"""
    if not root.is_dir():
        return {}
    out: dict[str, str] = {}
    for md in sorted(root.glob("*.md")):
        try:
            fm = _parse_frontmatter(md.read_text(encoding="utf-8"))
        except OSError:
            continue
        out[md.stem] = fm.get("description", "")
    return out


def _scan_plugins(plugins_root: Path) -> dict[str, str]:
    """读取 marketplace 的 skill 与 command；同名时 skill 优先。"""
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
                    continue
                out[f"{mp.name}:{sub.name}"] = fm.get("description", "")
        cmds_dir = mp / "commands"
        if cmds_dir.is_dir():
            for md in sorted(cmds_dir.glob("*.md")):
                try:
                    fm = _parse_frontmatter(md.read_text(encoding="utf-8"))
                except OSError:
                    continue
                key = f"{mp.name}:{md.stem}"
                if key not in out:
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
    """按 project、user、plugin、bundled、builtin、init floor 去重汇总。"""
    wd = Path(workdir)
    us = user_skills_dir or (Path.home() / ".claude" / "skills")
    uc = user_commands_dir or (Path.home() / ".claude" / "commands")
    ps = project_skills_dir or (wd / ".claude" / "skills")
    pc = project_commands_dir or (wd / ".claude" / "commands")

    seen: dict[str, SlashItem] = {}

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

    # init roster 只补名称；已扫描到的描述和来源始终优先。
    for name in init_roster or []:
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
