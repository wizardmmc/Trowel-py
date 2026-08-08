"""汇总 CC 的项目、用户、plugin、内置项和 init roster 中的 slash 补全项。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from trowel_py.cc_host.frontmatter import (
    parse_frontmatter as _run_parse_frontmatter,
)

# 内置描述可能落后于已安装的 CC；init roster 用于补齐缺少的名称。
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
    "effort": "切换 effort（回车弹出选择器：low/medium/high/xhigh/max）",
    "cost": "显示当前会话累计花费",
    "status": "显示当前模型 / effort / 进程状态",
}

# Headless CC 无法执行这些 TUI/debug 命令，因此只在补充 init roster 名称时过滤。
_CC_TUI_COMMANDS: frozenset[str] = frozenset({
    "clear", "compact", "config", "context", "heapdump", "reload-skills",
    "usage", "insights", "goal",
})


@dataclass(frozen=True)
class SlashItem:
    """表示 `/cc/slash-items` 返回的一条补全项。

    Attributes:
        name: 输入斜杠后使用的命令或 skill 名称；plugin 项包含 marketplace 前缀。
        description: frontmatter 或内置映射提供的说明；没有说明时为空字符串。
        source: 来源标记，取值为 `project`、`user`、`plugin`、`bundled` 或
            `builtin`。
        type: 补全项类别，记录为 `skill` 或 `command`。
    """

    name: str
    description: str
    source: str
    type: str


def _parse_frontmatter(text: str) -> dict[str, str]:
    """通过共享解析器读取 skill 或 command 的简化 frontmatter。

    Args:
        text: 要解析的 Markdown 文本。

    Returns:
        解析出的字符串键值；没有完整 frontmatter 时返回空字典。
    """

    return _run_parse_frontmatter(text)


def _scan_skills(root: Path) -> dict[str, str]:
    """扫描目录下各 skill 子目录的 `SKILL.md`。

    frontmatter 缺少 `name` 时使用子目录名，缺少 `description` 时使用空字符串；
    根目录缺失、条目不是目录、缺少 `SKILL.md` 或文件不可读时跳过。

    Args:
        root: 直接包含各 skill 子目录的目录。

    Returns:
        skill 名称与说明的对应表。
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
            continue
        name = fm.get("name") or sub.name
        out[name] = fm.get("description", "")
    return out


def _scan_commands(root: Path) -> dict[str, str]:
    """扫描目录下的 command Markdown，并以文件名主体作为命令名。

    缺少 `description` 时使用空字符串；根目录缺失或文件不可读时跳过。

    Args:
        root: 直接包含 command Markdown 的目录。

    Returns:
        命令名与说明的对应表。
    """
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
    """扫描各 marketplace 的 skill 和 command，并添加 marketplace 名称前缀。

    每项名称使用 `<marketplace>:<目录或文件名>`；同名 skill 和 command 同时存在时
    保留 skill。目录、定义文件缺失或定义文件不可读时跳过。

    Args:
        plugins_root: 包含 `marketplaces` 子目录的 CC plugin 根目录。

    Returns:
        带 marketplace 前缀的补全项名称与说明对应表。
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
    """按来源优先级合并、去重并排序 CC slash 补全项。

    同名项只保留最先出现者，优先级依次为 project skill、project command、user
    skill、user command、plugin、bundled、builtin 和 init roster。init roster 只
    补名称，过滤 `mcp__` 工具和 headless CC 无法执行的 TUI/debug 命令。结果按
    `type`、`name` 升序排列。

    Args:
        workdir: 用于查找 `.claude/skills` 和 `.claude/commands` 的项目目录。
        user_skills_dir: 用户 skill 目录；`None` 使用 `~/.claude/skills`。
        user_commands_dir: 用户 command 目录；`None` 使用 `~/.claude/commands`。
        project_skills_dir: 项目 skill 目录；`None` 使用工作目录下的默认路径。
        project_commands_dir: 项目 command 目录；`None` 使用工作目录下的默认路径。
        plugins_dir: CC plugin 根目录；`None` 使用 `~/.claude/plugins`。
        init_roster: CC init 消息报告的 slash 名称；`None` 表示不补充。

    Returns:
        按类别和名称排序且名称唯一的补全项。
    """
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
