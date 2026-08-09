#!/usr/bin/env python3
"""检查公共 Agent 上下文的跟踪、链接、权威命令和基础隐私边界。"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.shared_context.commands import (  # noqa: E402
    audit_authoritative_command_targets,
    inspect_authoritative_commands,
)
from scripts.shared_context.markdown import (  # noqa: E402
    MarkdownTarget,
    claude_imports,
    has_raw_html,
    markdown_attribute_values,
    markdown_headings,
    markdown_links as markdown_links,
    markdown_section_tables,
    markdown_targets,
    markdown_visible_text,
    module_status_evidence_fields,
    root_entrypoint_targets,
    root_module_index_entries,
    unsupported_raw_html,
)
from scripts.shared_context.models import (  # noqa: E402
    ContextFinding,
    ModuleContextPolicy,
)


ROOT_CONTEXT_FILES: tuple[Path, ...] = tuple(
    Path(value)
    for value in (
        "AGENTS.md",
        "CLAUDE.md",
        "README.md",
        "directory.md",
        "docs/foundation/prd.md",
        "docs/foundation/development.md",
        "docs/foundation/front-end-design-language.md",
        "docs/reference/agent-runtime.md",
        "docs/reference/memory-runtime.md",
    )
)

MODULE_CONTEXT_POLICIES: tuple[ModuleContextPolicy, ...] = (
    ModuleContextPolicy(
        root=Path("trowel_py/discussion"),
        required_headings=(
            "领域职责与 owner",
            "不可破坏的不变量",
            "Agent runtime、Memory、Profile 与 telemetry 边界",
            "运行事实状态",
            "权威测试入口",
            "继续阅读",
        ),
        required_status_groups=(
            ("verified_live", "compatibility_required"),
            ("unknown",),
        ),
        authoritative_command_sections=("权威测试入口",),
    ),
)
MODULE_CONTEXT_ROOTS: tuple[Path, ...] = tuple(
    policy.root for policy in MODULE_CONTEXT_POLICIES
)
MODULE_CONTEXT_REQUIRED_HEADINGS: dict[Path, tuple[str, ...]] = {
    policy.root: policy.required_headings for policy in MODULE_CONTEXT_POLICIES
}
MODULE_CONTEXT_REQUIRED_STATUS_GROUPS: dict[Path, tuple[tuple[str, ...], ...]] = {
    policy.root: policy.required_status_groups for policy in MODULE_CONTEXT_POLICIES
}

ROOT_INDEX_FILES: tuple[Path, ...] = (
    Path("README.md"),
    Path("directory.md"),
    Path("docs/foundation/prd.md"),
    Path("docs/foundation/development.md"),
)

# 这些文件是跨 checkout 审计的固定 bootstrap；重命名或删除时必须同步更新。
_REQUIRED_SHARED_CONTEXT_AUDIT_PACKAGE_FILES: tuple[Path, ...] = (
    Path("scripts/shared_context/__init__.py"),
    Path("scripts/shared_context/commands.py"),
    Path("scripts/shared_context/markdown.py"),
    Path("scripts/shared_context/models.py"),
)


def _shared_context_audit_package_files(repo_root: Path) -> tuple[Path, ...]:
    """返回目标仓库审计包内全部 Python 文件。

    Args:
        repo_root: 需要发现审计实现闭包的目标仓库根目录。

    Returns:
        按路径排序的仓库相对 Python 文件；包缺失或为符号链接时为空元组。
    """

    package_root = repo_root / "scripts/shared_context"
    if not package_root.is_dir() or package_root.is_symlink():
        return ()
    return tuple(
        source_file.relative_to(repo_root)
        for source_file in sorted(package_root.rglob("*.py"))
        if source_file.is_file() or source_file.is_symlink()
    )


def _shared_context_audit_package_symlinks(repo_root: Path) -> tuple[Path, ...]:
    """返回目标仓库审计包根及其后代中的全部符号链接。

    Args:
        repo_root: 需要检查审计实现物理边界的目标仓库根目录。

    Returns:
        按路径排序的仓库相对符号链接；正常目录且没有链接时为空元组。
    """

    package_root = repo_root / "scripts/shared_context"
    if package_root.is_symlink():
        return (Path("scripts/shared_context"),)
    if not package_root.is_dir():
        return ()
    return tuple(
        path.relative_to(repo_root)
        for path in sorted(package_root.rglob("*"))
        if path.is_symlink()
    )


def _audit_control_files(repo_root: Path) -> tuple[Path, ...]:
    """返回目标仓库必须由 Git 恢复的审计实现与契约测试闭包。

    Args:
        repo_root: 需要组合严格控制文件闭包的目标仓库根目录。

    Returns:
        Git 策略、审计入口、递归包文件和契约测试的仓库相对路径。
    """

    discovered_package_files = _shared_context_audit_package_files(repo_root)
    package_files = tuple(
        dict.fromkeys(
            (
                *_REQUIRED_SHARED_CONTEXT_AUDIT_PACKAGE_FILES,
                *discovered_package_files,
            )
        )
    )
    return (
        Path(".gitignore"),
        Path(".worktreeinclude"),
        Path("scripts/__init__.py"),
        *package_files,
        Path("scripts/shared_context_check.py"),
        Path("tests/foundation/test_project_context_workflow.py"),
        Path("tests/foundation/test_shared_context_commands.py"),
        Path("tests/foundation/test_shared_context.py"),
    )


_SOURCE_REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SHARED_CONTEXT_AUDIT_PACKAGE_FILES = _shared_context_audit_package_files(
    _SOURCE_REPOSITORY_ROOT
)
AUDIT_CONTROL_FILES = _audit_control_files(_SOURCE_REPOSITORY_ROOT)

AUTHORITATIVE_COMMAND_SECTIONS: dict[Path, tuple[str, ...]] = {
    Path("AGENTS.md"): ("常用验证",),
    **{
        policy.root / "AGENTS.md": policy.authoritative_command_sections
        for policy in MODULE_CONTEXT_POLICIES
    },
}
PYTHON_COMMAND_MODULE_ROOTS = frozenset(
    {"scripts", "tests", *(policy.root.parts[0] for policy in MODULE_CONTEXT_POLICIES)}
)

CONTEXT_DISCOVERY_EXCLUDED_ROOTS: tuple[Path, ...] = tuple(
    Path(value)
    for value in (
        ".git",
        ".venv",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".vite",
        ".playwright-mcp",
        ".playwright-cli",
        ".gstack",
        ".claude/worktrees",
        "build",
        "dist",
        "spikes",
        "trowel_py/static",
        "web/.release",
        "web/desktop-dist",
        "web/dist",
        "web/node_modules",
        "web/out",
    )
)

SHARED_CONTEXT_FILES: tuple[Path, ...] = (
    *ROOT_CONTEXT_FILES,
    *(
        module_root / filename
        for module_root in MODULE_CONTEXT_ROOTS
        for filename in ("AGENTS.md", "CLAUDE.md")
    ),
)

PRIVATE_CONTEXT_PATHS: tuple[Path, ...] = tuple(
    Path(value)
    for value in (
        "CLAUDE.local.md",
        "CLAUDE-archived.md",
        "Learn.md",
        "config.toml",
        "progress.txt",
        "spikes",
        "docs/README.md",
        "docs/slices",
        "docs/milestones",
        "docs/design",
        "docs/experiments",
        "docs/archive",
    )
)

_PRIVATE_IGNORE_SENTINEL = ".shared-context-private"
PRIVATE_IGNORE_PROBES: tuple[Path, ...] = tuple(
    path if path.suffix else path / _PRIVATE_IGNORE_SENTINEL
    for path in PRIVATE_CONTEXT_PATHS
)

_PRIVACY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "absolute_user_path",
        re.compile(
            r"(?<![A-Za-z0-9])/(?:Users|home)/"
            r"[^/\s`'\"，。；：！？,;:!?]+(?:/|(?=[\s`'\"，。；：！？,;:!?\])}]|$))"
        ),
    ),
    (
        "windows_user_path",
        re.compile(
            r"(?i)(?<![A-Za-z0-9])[A-Z]:\\Users\\"
            r"[^\\\s`'\"，。；：！？,;:!?]+(?:\\|(?=[\s`'\"，。；：！？,;:!?\])}]|$))"
        ),
    ),
    (
        "credential_shape",
        re.compile(
            r"(?<![A-Za-z0-9])(?:sk-(?:ant-)?[A-Za-z0-9_-]{20,}|"
            r"gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})(?![A-Za-z0-9])"
        ),
    ),
    (
        "uuid",
        re.compile(
            r"(?i)(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}(?![0-9a-f])"
        ),
    ),
)

_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
_MEDIA_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"})
_PUBLIC_TEXT_LEAVES = frozenset({Path("LICENSE"), Path("COPYRIGHT")})
_REGULAR_BLOB_MODES = frozenset({"100644", "100755"})
_MODULE_FACT_STATUSES = frozenset(
    {"verified_live", "compatibility_required", "unknown", "removal_candidate"}
)
_MODULE_STATUS_EVIDENCE_FIELDS: dict[str, dict[str, frozenset[str]]] = {
    "verified_live": {
        "present": frozenset(
            {"record", "date", "commit", "platform", "runtime", "config", "scope"}
        ),
        "absent": frozenset({"record", "checked", "gap", "do_not_claim", "exit"}),
    },
    "compatibility_required": {
        "present": frozenset({"record", "contract", "commit", "tests"}),
    },
    "unknown": {
        "present": frozenset({"record", "checked", "gap", "do_not_claim", "exit"}),
    },
    "removal_candidate": {
        "present": frozenset(
            {"record", "live", "tests", "business", "human_review", "next"}
        ),
        "absent": frozenset({"record", "checked", "gap", "do_not_claim", "exit"}),
    },
}


def _run_git(repo_root: Path, *args: str, input_text: str | None = None) -> str:
    """在目标仓库运行只读 Git 命令并返回标准输出。

    Args:
        repo_root: 被检查仓库的根目录。
        *args: 传给 Git 的完整参数序列。
        input_text: 可选的标准输入正文。

    Returns:
        去掉末尾换行的标准输出。
    """

    completed = subprocess.run(
        ("git", *args),
        cwd=repo_root,
        input=input_text,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.rstrip("\n")


def _tracked_file_modes(repo_root: Path) -> dict[Path, str]:
    """返回仓库索引中全部已跟踪路径及其 Git mode。

    Args:
        repo_root: 需要查询 Git 索引的仓库根目录。

    Returns:
        以仓库根目录为基准的路径到 Git mode 映射。
    """

    output = _run_git(repo_root, "ls-files", "--stage", "-z")
    modes: dict[Path, str] = {}
    for record in output.split("\0"):
        if not record:
            continue
        metadata, relative_path = record.split("\t", 1)
        mode, _object_id, stage = metadata.split(" ", 2)
        if stage == "0":
            modes[Path(relative_path)] = mode
    return modes


def _worktree_changed_files(repo_root: Path) -> set[Path]:
    """返回相对 Git 索引存在未暂存差异的路径。

    Args:
        repo_root: 需要比较索引与工作树的仓库根目录。

    Returns:
        正文、类型或存在性在索引和工作树之间不同的仓库相对路径。
    """

    output = _run_git(repo_root, "diff", "--name-only", "-z", "--")
    return {Path(value) for value in output.split("\0") if value}


def _special_index_flags(repo_root: Path) -> dict[Path, str]:
    """返回会让普通 Git diff 隐藏工作树差异的索引标志。

    Args:
        repo_root: 需要查询 stage-0 索引标志的仓库根目录。

    Returns:
        设置了 ``assume-unchanged`` 或 ``skip-worktree`` 的路径及 Git 标签。
    """

    output = _run_git(repo_root, "ls-files", "-v", "-z")
    flags: dict[Path, str] = {}
    for record in output.split("\0"):
        if len(record) < 3 or record[1] != " ":
            continue
        tag = record[0]
        if tag == "S" or tag.islower():
            flags[Path(record[2:])] = tag
    return flags


def _repository_context_files(
    repo_root: Path, tracked_files: set[Path]
) -> tuple[set[Path], set[Path]]:
    """返回全部已跟踪和排除树之外工作区内的 Agent 说明文件。

    Args:
        repo_root: 需要查询工作树的仓库根目录。
        tracked_files: Git 索引中的全部路径；排除目录内的已跟踪说明也必须审计。

    Returns:
        第一项是全部已跟踪文件，加上排除树之外工作区内的 ``AGENTS.md``、
        ``AGENTS.override.md``、``CLAUDE.md``、``CLAUDE.local.md`` 和 Claude rules；
        以及 Codex 项目级 ``.codex/config.toml``，不受 Git ignore 影响。第二项是
        非排除树中的目录符号链接。
    """

    result = {path for path in tracked_files if _runtime_context_path(path)}
    symlink_directories: set[Path] = set()
    for directory, child_directories, files in os.walk(repo_root):
        relative_directory = Path(directory).relative_to(repo_root)
        traversable_children: list[str] = []
        for child in child_directories:
            relative_child = relative_directory / child
            if _context_discovery_excluded(relative_child):
                continue
            if (repo_root / relative_child).is_symlink():
                symlink_directories.add(relative_child)
                continue
            traversable_children.append(child)
        child_directories[:] = traversable_children
        result.update(
            relative_directory / filename
            for filename in files
            if _runtime_context_path(relative_directory / filename)
        )
    return result, symlink_directories


def _runtime_context_path(relative_path: Path) -> bool:
    """判断路径是否会被当前 Claude Code 或 Codex 当作项目说明加载。"""

    if relative_path.name.casefold() in {
        "agents.md",
        "agents.override.md",
        "claude.md",
        "claude.local.md",
    }:
        return True
    parts = relative_path.parts
    if relative_path.name.casefold() == "config.toml" and any(
        part.casefold() == ".codex" for part in parts
    ):
        return True
    return relative_path.suffix.casefold() == ".md" and any(
        len(parts) > index + 2 and parts[index + 1].casefold() == "rules"
        for index, part in enumerate(parts)
        if part.casefold() == ".claude"
    )


def _claude_only_context(relative_path: Path) -> bool:
    """判断路径是否是会形成第二事实源的 Claude 专属仓库说明。"""

    return any(part.casefold() == ".claude" for part in relative_path.parts) and (
        _runtime_context_path(relative_path)
    )


def _codex_only_context(relative_path: Path) -> bool:
    """判断路径是否会配置 Codex 专属项目说明或运行行为。"""

    return relative_path.name.casefold() == "config.toml" and any(
        part.casefold() == ".codex" for part in relative_path.parts
    )


def _path_at_or_below_casefold(relative_path: Path, root: Path) -> bool:
    """按大小写不敏感语义判断路径是否等于或位于给定根目录下。"""

    path_parts = tuple(part.casefold() for part in relative_path.parts)
    root_parts = tuple(part.casefold() for part in root.parts)
    return path_parts[: len(root_parts)] == root_parts


def _context_discovery_excluded(relative_path: Path) -> bool:
    """判断目录是否属于不会承载项目说明的依赖、产物或私有实验树。"""

    return any(
        _path_at_or_below_casefold(relative_path, excluded)
        for excluded in CONTEXT_DISCOVERY_EXCLUDED_ROOTS
    )


def _repository_command_path_roots(
    repo_root: Path, tracked_files: set[Path]
) -> frozenset[str]:
    """返回权威命令允许直接引用的当前仓库一级目录。

    Args:
        repo_root: 被审计仓库的根目录。
        tracked_files: Git 索引中的文件路径。

    Returns:
        Git 已跟踪或当前工作树中非隐藏的一级目录名。
    """

    tracked_roots = {path.parts[0] for path in tracked_files if len(path.parts) > 1}
    visible_roots = {
        path.name
        for path in repo_root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    }
    return frozenset(tracked_roots | visible_roots)


def _repository_python_package_roots(repo_root: Path) -> frozenset[Path]:
    """返回仓库 Python 源码根中由 ``__init__.py`` 证明存在的包目录。

    Args:
        repo_root: 被审计仓库的根目录。

    Returns:
        相对仓库根目录的包路径，不跟随符号链接文件。
    """

    packages: set[Path] = set()
    for root_name in PYTHON_COMMAND_MODULE_ROOTS:
        source_root = repo_root / root_name
        if not source_root.is_dir() or source_root.is_symlink():
            continue
        packages.update(
            init_file.parent.relative_to(repo_root)
            for init_file in source_root.rglob("__init__.py")
            if not init_file.is_symlink()
        )
    return frozenset(packages)


def _ignored_files(
    repo_root: Path,
    relative_paths: Iterable[Path],
    tracked_files: set[Path],
) -> set[Path]:
    """批量返回被当前 Git ignore 规则排除的路径。

    Args:
        repo_root: 需要应用 ignore 规则的 Git 仓库根目录。
        relative_paths: 以仓库根目录为基准的待查路径。
        tracked_files: Git 索引中的路径，用于确认 ignore 规则也由仓库持久保存。

    Returns:
        命中 ignore 规则的仓库相对路径集合。
    """

    ignored: set[Path] = set()
    for source, pattern, matched_path in _git_ignore_matches(
        repo_root,
        relative_paths,
        include_tracked=True,
        repository_rules_only=True,
    ):
        if (
            source.name == ".gitignore"
            and source in tracked_files
            and not pattern.startswith("!")
        ):
            ignored.add(matched_path)
    return ignored


def _all_git_ignored_files(
    repo_root: Path, relative_paths: Iterable[Path]
) -> set[Path]:
    """返回被仓库、`.git/info/exclude` 或全局规则忽略的未跟踪路径。

    Args:
        repo_root: 需要应用完整 Git ignore 配置的仓库根目录。
        relative_paths: 以仓库根目录为基准的待查路径。

    Returns:
        命中任一当前 Git ignore 来源的未跟踪路径集合。
    """

    return {
        matched_path
        for _source, pattern, matched_path in _git_ignore_matches(
            repo_root,
            relative_paths,
            include_tracked=False,
            repository_rules_only=False,
        )
        if not pattern.startswith("!")
    }


def _git_ignore_matches(
    repo_root: Path,
    relative_paths: Iterable[Path],
    *,
    include_tracked: bool,
    repository_rules_only: bool,
) -> tuple[tuple[Path, str, Path], ...]:
    """批量读取 Git 对路径给出的最终 ignore 规则。

    Args:
        repo_root: 执行 ``git check-ignore`` 的仓库根目录。
        relative_paths: 以仓库根目录为基准的待查路径。
        include_tracked: 是否用 ``--no-index`` 同时检查已跟踪路径。
        repository_rules_only: 是否禁用用户级全局 ignore 文件。

    Returns:
        每项包含规则来源、规则正文和命中路径；未命中时为空元组。
    """

    paths = tuple(dict.fromkeys(relative_paths))
    if not paths:
        return ()
    command = ["git"]
    if repository_rules_only:
        command.extend(("-c", "core.excludesFile=/dev/null"))
    command.extend(("check-ignore", "-v", "--stdin", "-z"))
    if include_tracked:
        command.append("--no-index")
    completed = subprocess.run(
        command,
        cwd=repo_root,
        input="".join(f"{path}\0" for path in paths),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode not in (0, 1):
        raise RuntimeError(f"git check-ignore failed: {completed.stderr.strip()}")
    fields = [value for value in completed.stdout.split("\0") if value]
    return tuple(
        (Path(fields[index]), fields[index + 2], Path(fields[index + 3]))
        for index in range(0, len(fields), 4)
    )




def privacy_matches(markdown: str) -> tuple[tuple[str, str], ...]:
    """返回公共正文命中的确定性隐私或凭据形态。

    Args:
        markdown: 需要扫描的公共 Markdown 正文。

    Returns:
        按规则和正文顺序排列的问题代码与命中片段。
    """

    matches: list[tuple[str, str]] = []
    for code, pattern in _PRIVACY_PATTERNS:
        matches.extend((code, match.group(0)) for match in pattern.finditer(markdown))
    return tuple(matches)


def _private_path(relative_path: Path) -> bool:
    """判断路径是否属于冻结的私人上下文范围。

    Args:
        relative_path: 待判定的仓库相对路径。

    Returns:
        路径本身或其父目录属于私人范围时为 True。
    """

    return any(
        _path_at_or_below_casefold(relative_path, private_path)
        for private_path in PRIVATE_CONTEXT_PATHS
    )


def _path_has_symlink(repo_root: Path, relative_path: Path) -> bool:
    """判断仓库相对路径本身或任一父组件是否为符号链接。

    Args:
        repo_root: 已解析的仓库根目录。
        relative_path: 不包含仓库根的词法路径。

    Returns:
        任一路径组件是符号链接时为 True。
    """

    current = repo_root
    for part in relative_path.parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def _reachable_markdown_path(relative_path: Path) -> bool:
    """判断导航链接目标是否应进入公共 Markdown 可达闭包。"""

    return relative_path.suffix.casefold() in _MARKDOWN_SUFFIXES


def _strict_context_path(relative_path: Path) -> bool:
    """判断公共 Markdown 是否属于禁止原始 HTML 的项目事实层。"""

    strict_roots = (Path("docs/foundation"), Path("docs/reference"))
    return (
        relative_path in SHARED_CONTEXT_FILES and relative_path != Path("README.md")
    ) or any(_path_at_or_below_casefold(relative_path, root) for root in strict_roots)


def _resolve_local_link(
    repo_root: Path, source: Path, target: str
) -> tuple[Path | None, str | None]:
    """把本地 Markdown 链接解析成仓库相对路径。

    Args:
        repo_root: 被检查仓库的根目录。
        source: 含链接的仓库相对 Markdown 路径。
        target: Markdown 解析器返回的原始链接目标。

    Returns:
        第一个元素是本地目标的仓库相对路径；外部链接和纯锚点为 None。
        第二个元素在目标越出仓库时说明错误，正常时为 None。
    """

    parsed = urlsplit(target)
    if parsed.scheme:
        if parsed.scheme.lower() in {"http", "https", "mailto"}:
            return None, None
        return None, f"unsupported link scheme: {target}"
    if parsed.netloc:
        return None, f"scheme-relative link is not allowed: {target}"
    if not parsed.path:
        return None, None
    decoded_path = unquote(parsed.path)
    resolved_root = repo_root.resolve()
    raw_path = Path(decoded_path)
    if raw_path.is_absolute():
        lexical_candidate = Path(os.path.normpath(decoded_path))
    else:
        lexical_candidate = Path(
            os.path.normpath(str(resolved_root / source.parent / decoded_path))
        )
    try:
        lexical_path = lexical_candidate.relative_to(resolved_root)
    except ValueError:
        return None, f"link escapes repository: {target}"
    try:
        lexical_candidate.resolve().relative_to(resolved_root)
    except ValueError:
        return None, f"link escapes repository through symlink: {target}"
    return lexical_path, None


def audit_repository(
    repo_root: Path, *, require_tracked: bool = True
) -> tuple[ContextFinding, ...]:
    """审计仓库的共享 Agent 上下文边界。

    Args:
        repo_root: 被检查 Git 仓库的根目录。
        require_tracked: 是否要求公共文件已经进入 Git 索引。未提交开发树可关闭该项，
            clean checkout 和 CI 必须保持开启。

    Returns:
        按检查顺序排列的全部阻断问题。
    """

    repo_root = repo_root.resolve()
    audit_control_files = _audit_control_files(repo_root)
    audit_package_symlinks = set(_shared_context_audit_package_symlinks(repo_root))
    tracked_modes = _tracked_file_modes(repo_root)
    tracked = set(tracked_modes)
    ignore_policy_files = {
        path for path in tracked if path.name.casefold() == ".gitignore"
    }
    worktree_changed = _worktree_changed_files(repo_root) if require_tracked else set()
    special_index_flags = _special_index_flags(repo_root) if require_tracked else {}
    findings: list[ContextFinding] = []
    shared_markdown: dict[Path, str] = {}
    resolved_links: dict[
        Path, list[tuple[MarkdownTarget, Path | None, str | None]]
    ] = {}
    link_paths: list[Path] = []

    for relative_path in sorted(audit_package_symlinks):
        findings.append(
            ContextFinding(
                "nonregular_audit_control",
                relative_path,
                "shared context audit package must not contain symlinks",
            )
        )

    if require_tracked:
        for relative_path in audit_control_files:
            if relative_path in audit_package_symlinks:
                continue
            if relative_path not in tracked:
                findings.append(
                    ContextFinding(
                        "untracked_audit_control",
                        relative_path,
                        "strict audit control files must be tracked by Git",
                    )
                )
            elif (
                tracked_modes[relative_path] not in _REGULAR_BLOB_MODES
                or _path_has_symlink(repo_root, relative_path)
                or not (repo_root / relative_path).is_file()
            ):
                findings.append(
                    ContextFinding(
                        "nonregular_audit_control",
                        relative_path,
                        "strict audit control files must be regular Git blobs",
                    )
                )
        for relative_path in sorted(ignore_policy_files - set(audit_control_files)):
            if (
                tracked_modes[relative_path] not in _REGULAR_BLOB_MODES
                or _path_has_symlink(repo_root, relative_path)
                or not (repo_root / relative_path).is_file()
            ):
                findings.append(
                    ContextFinding(
                        "nonregular_ignore_policy",
                        relative_path,
                        "tracked .gitignore files must be regular Git blobs",
                    )
                )

    repository_context_files, symlink_context_directories = _repository_context_files(
        repo_root, tracked
    )
    for relative_path in sorted(symlink_context_directories):
        findings.append(
            ContextFinding(
                "symlink_context_directory",
                relative_path,
                "repository context discovery does not allow directory symlinks",
            )
        )
    for relative_path in sorted(
        path
        for path in repository_context_files
        if path.name.casefold() == "agents.override.md"
    ):
        findings.append(
            ContextFinding(
                "forbidden_agents_override",
                relative_path,
                "AGENTS.override.md would shadow the shared AGENTS.md fact source",
            )
        )
    for relative_path in sorted(
        path
        for path in repository_context_files
        if path.name.casefold() != "claude.local.md" and _claude_only_context(path)
    ):
        findings.append(
            ContextFinding(
                "forbidden_claude_only_context",
                relative_path,
                "repository Claude-only instructions would duplicate shared AGENTS.md",
            )
        )
    for relative_path in sorted(
        path for path in repository_context_files if _codex_only_context(path)
    ):
        findings.append(
            ContextFinding(
                "forbidden_codex_only_context",
                relative_path,
                "repository .codex/config.toml would create a Codex-only project source",
            )
        )
    for relative_path in sorted(
        path
        for path in repository_context_files
        if path.name.casefold() == "claude.local.md" and path != Path("CLAUDE.local.md")
    ):
        findings.append(
            ContextFinding(
                "forbidden_nested_claude_local",
                relative_path,
                "only the ignored repository-root CLAUDE.local.md is allowed",
            )
        )
    for relative_path in sorted(
        path
        for path in repository_context_files - set(SHARED_CONTEXT_FILES)
        if path.name.casefold() not in {"agents.override.md", "claude.local.md"}
        and not _claude_only_context(path)
        and not _codex_only_context(path)
    ):
        findings.append(
            ContextFinding(
                "unregistered_module_context",
                relative_path,
                "nested AGENTS.md or CLAUDE.md must be registered for shared audit",
            )
        )

    pending_context_files = list(SHARED_CONTEXT_FILES)
    queued_context_files = set(SHARED_CONTEXT_FILES)
    symlink_context_files: set[Path] = set()
    while pending_context_files:
        relative_path = pending_context_files.pop(0)
        absolute_path = repo_root / relative_path
        if _path_has_symlink(repo_root, relative_path):
            symlink_context_files.add(relative_path)
            continue
        if not absolute_path.is_file():
            continue
        markdown = absolute_path.read_text(encoding="utf-8")
        shared_markdown[relative_path] = markdown
        resolved_links[relative_path] = []
        for target in markdown_targets(markdown):
            linked_path, error = _resolve_local_link(
                repo_root, relative_path, target.target
            )
            resolved_links[relative_path].append((target, linked_path, error))
            if linked_path is not None:
                link_paths.append(linked_path)
            if (
                not target.is_image
                and linked_path is not None
                and _reachable_markdown_path(linked_path)
                and not _private_path(linked_path)
                and linked_path not in queued_context_files
                and (repo_root / linked_path).is_file()
            ):
                queued_context_files.add(linked_path)
                pending_context_files.append(linked_path)

    command_inspection = inspect_authoritative_commands(
        shared_markdown,
        AUTHORITATIVE_COMMAND_SECTIONS,
        repository_path_roots=_repository_command_path_roots(repo_root, tracked),
        module_path_roots=PYTHON_COMMAND_MODULE_ROOTS,
        python_package_roots=_repository_python_package_roots(repo_root),
    )
    command_targets = command_inspection.targets
    command_target_paths = tuple(target.target for target in command_targets)

    private_files = sorted(
        {
            path.relative_to(repo_root)
            for path in (repo_root / "docs").rglob("*")
            if path.is_file()
            and path.relative_to(repo_root) not in shared_markdown
            and path.relative_to(repo_root) not in link_paths
        }
        | set(PRIVATE_IGNORE_PROBES)
    )
    all_audited_paths = (
        *SHARED_CONTEXT_FILES,
        *shared_markdown,
        *link_paths,
        *command_target_paths,
        *private_files,
    )
    repository_ignored = _ignored_files(
        repo_root,
        all_audited_paths,
        tracked,
    )
    publishability_ignored = _all_git_ignored_files(repo_root, all_audited_paths)

    bridge_paths = (
        Path("CLAUDE.md"),
        *(module_root / "CLAUDE.md" for module_root in MODULE_CONTEXT_ROOTS),
    )

    invalid_linked_contexts = {
        path
        for path in shared_markdown
        if path not in SHARED_CONTEXT_FILES
        and (_private_path(path) or path in publishability_ignored)
    }

    public_docs = (set(shared_markdown) - invalid_linked_contexts) | {
        path
        for path in link_paths
        if not _private_path(path) and path not in publishability_ignored
    }
    tracked_private_files = {path for path in tracked if _private_path(path)} | {
        path
        for path in tracked
        if path.parts and path.parts[0].casefold() == "docs" and path not in public_docs
    }
    for relative_path in sorted(tracked_private_files):
        findings.append(
            ContextFinding(
                "tracked_private_file",
                relative_path,
                "private project context must not be tracked by Git",
            )
        )

    for relative_path in sorted(symlink_context_files):
        findings.append(
            ContextFinding(
                "symlink_shared_file",
                relative_path,
                "shared context files must be regular files inside the repository",
            )
        )

    for relative_path in SHARED_CONTEXT_FILES:
        absolute_path = repo_root / relative_path
        if relative_path in symlink_context_files:
            continue
        if not absolute_path.is_file():
            findings.append(
                ContextFinding("missing_shared_file", relative_path, "file is missing")
            )
            continue
    for relative_path, markdown in shared_markdown.items():
        if relative_path in invalid_linked_contexts:
            continue
        if require_tracked and relative_path not in tracked:
            findings.append(
                ContextFinding(
                    "untracked_shared_file", relative_path, "file is not tracked by Git"
                )
            )
        elif (
            relative_path in tracked_modes
            and tracked_modes[relative_path] not in _REGULAR_BLOB_MODES
        ):
            findings.append(
                ContextFinding(
                    "nonregular_shared_file",
                    relative_path,
                    "shared context must be stored as a regular Git blob",
                )
            )
        if relative_path in publishability_ignored:
            findings.append(
                ContextFinding(
                    "ignored_shared_file", relative_path, "file is still ignored"
                )
            )
        raw_html = has_raw_html(markdown)
        unsupported_html = unsupported_raw_html(markdown) if raw_html else ()
        if raw_html and (_strict_context_path(relative_path) or unsupported_html):
            detail = (
                "raw HTML is forbidden in shared project facts"
                if _strict_context_path(relative_path)
                else f"unsupported raw HTML: {unsupported_html[0]}"
            )
            findings.append(
                ContextFinding(
                    "forbidden_raw_html",
                    relative_path,
                    detail,
                )
            )
        seen_privacy_matches: set[tuple[str, str]] = set()
        privacy_sources = (
            markdown,
            markdown_visible_text(markdown),
            *(
                unquote(target.target)
                for target, _linked_path, _error in resolved_links[relative_path]
            ),
            *(unquote(value) for value in markdown_attribute_values(markdown)),
        )
        for privacy_source in privacy_sources:
            for code, matched in privacy_matches(privacy_source):
                match_key = (code, matched)
                if match_key in seen_privacy_matches:
                    continue
                seen_privacy_matches.add(match_key)
                findings.append(
                    ContextFinding(code, relative_path, f"matched {matched!r}")
                )
        for imported_path in claude_imports(markdown):
            if relative_path in bridge_paths and imported_path == "AGENTS.md":
                continue
            findings.append(
                ContextFinding(
                    "forbidden_claude_import",
                    relative_path,
                    f"only minimal CLAUDE.md bridges may import files: @{imported_path}",
                )
            )
        for target, linked_path, error in resolved_links[relative_path]:
            if error:
                findings.append(
                    ContextFinding("invalid_local_link", relative_path, error)
                )
                continue
            if linked_path is None:
                continue
            if _path_has_symlink(repo_root, linked_path):
                findings.append(
                    ContextFinding(
                        "symlink_link_target",
                        relative_path,
                        f"public links must not traverse symlinks: {target.target}",
                    )
                )
                continue
            if target.is_image and _reachable_markdown_path(linked_path):
                findings.append(
                    ContextFinding(
                        "markdown_image_target",
                        relative_path,
                        f"Markdown documents must use navigation links: {target.target}",
                    )
                )
                continue
            if target.is_image and linked_path.suffix.casefold() not in _MEDIA_SUFFIXES:
                findings.append(
                    ContextFinding(
                        "unsupported_media_target",
                        relative_path,
                        f"unsupported media target: {target.target}",
                    )
                )
                continue
            if (
                not target.is_image
                and not _reachable_markdown_path(linked_path)
                and linked_path not in _PUBLIC_TEXT_LEAVES
            ):
                findings.append(
                    ContextFinding(
                        "unsupported_navigation_target",
                        relative_path,
                        f"project context must use Markdown: {target.target}",
                    )
                )
                continue
            if _private_path(linked_path):
                findings.append(
                    ContextFinding(
                        "private_link",
                        relative_path,
                        f"link points to private path: {target.target}",
                    )
                )
                continue
            linked_absolute = repo_root / linked_path
            if not linked_absolute.exists():
                findings.append(
                    ContextFinding(
                        "missing_link_target",
                        relative_path,
                        f"link target does not exist: {target.target}",
                    )
                )
            elif not linked_absolute.is_file():
                findings.append(
                    ContextFinding(
                        "nonregular_link_target",
                        relative_path,
                        f"public link target must be a regular file: {target.target}",
                    )
                )
            elif (
                linked_path in tracked_modes
                and tracked_modes[linked_path] not in _REGULAR_BLOB_MODES
            ):
                findings.append(
                    ContextFinding(
                        "nonregular_link_target",
                        relative_path,
                        f"public link target must be a regular Git blob: {target.target}",
                    )
                )
            elif linked_path in publishability_ignored:
                findings.append(
                    ContextFinding(
                        "ignored_link_target",
                        relative_path,
                        f"link target is ignored: {target.target}",
                    )
                )
            elif require_tracked and linked_path not in tracked:
                findings.append(
                    ContextFinding(
                        "untracked_link_target",
                        relative_path,
                        f"link target is not tracked: {target.target}",
                    )
                )

    for relative_path in private_files:
        if relative_path not in repository_ignored:
            findings.append(
                ContextFinding(
                    "exposed_private_file", relative_path, "private file is not ignored"
                )
            )

    for bridge_path in bridge_paths:
        claude_bridge = shared_markdown.get(bridge_path)
        if claude_bridge is None:
            continue
        if "@AGENTS.md" not in claude_bridge.splitlines():
            findings.append(
                ContextFinding(
                    "missing_claude_bridge",
                    bridge_path,
                    "CLAUDE.md must import @AGENTS.md on its own line",
                )
            )
        elif claude_bridge.strip() != "@AGENTS.md":
            findings.append(
                ContextFinding(
                    "nonminimal_claude_bridge",
                    bridge_path,
                    "CLAUDE.md must not duplicate shared AGENTS.md knowledge",
                )
            )

    for module_root, required_headings in MODULE_CONTEXT_REQUIRED_HEADINGS.items():
        module_agents = module_root / "AGENTS.md"
        module_markdown = shared_markdown.get(module_agents)
        if module_markdown is None:
            continue
        headings = set(markdown_headings(module_markdown))
        for required_heading in required_headings:
            if required_heading not in headings:
                findings.append(
                    ContextFinding(
                        "missing_module_section",
                        module_agents,
                        f"module context must contain section: {required_heading}",
                    )
                )
        status_tables = markdown_section_tables(module_markdown, "运行事实状态")
        status_rows = status_tables[0] if len(status_tables) == 1 else ()
        parsed_statuses: set[str] = set()
        positive_statuses: set[str] = set()
        valid_status_table = bool(
            len(status_tables) == 1
            and status_rows
            and len(status_rows[0]) >= 2
            and status_rows[0][0].strip() == "状态"
        )
        if valid_status_table:
            for row in status_rows[1:]:
                status = row[0].strip() if row else ""
                evidence_fields = (
                    module_status_evidence_fields(row[1]) if len(row) >= 2 else None
                )
                record = evidence_fields.get("record") if evidence_fields else None
                required_fields = _MODULE_STATUS_EVIDENCE_FIELDS.get(status, {}).get(
                    record or ""
                )
                if (
                    status not in _MODULE_FACT_STATUSES
                    or status in parsed_statuses
                    or evidence_fields is None
                    or required_fields is None
                    or not required_fields.issubset(evidence_fields)
                ):
                    valid_status_table = False
                    break
                parsed_statuses.add(status)
                if record == "present":
                    positive_statuses.add(status)
        if not valid_status_table:
            findings.append(
                ContextFinding(
                    "invalid_module_status_table",
                    module_agents,
                    "runtime fact table needs unique allowed statuses and non-empty evidence",
                )
            )
        for required_group in MODULE_CONTEXT_REQUIRED_STATUS_GROUPS.get(
            module_root, ()
        ):
            if positive_statuses.intersection(required_group):
                continue
            code = (
                "missing_module_unknown"
                if required_group == ("unknown",)
                else "missing_module_evidence_status"
            )
            findings.append(
                ContextFinding(
                    code,
                    module_agents,
                    f"module status table needs one of: {', '.join(required_group)}",
                )
            )

    findings.extend(
        audit_authoritative_command_targets(
            repo_root,
            command_targets,
            tracked_files=tracked,
            ignored_paths=publishability_ignored,
            nonregular_tracked_files={
                path
                for path, mode in tracked_modes.items()
                if mode not in _REGULAR_BLOB_MODES
            },
            require_tracked=require_tracked,
        )
    )
    findings.extend(command_inspection.findings)

    root_agent_navigation = [
        linked_path
        for target, linked_path, error in resolved_links.get(Path("AGENTS.md"), ())
        if not target.is_image and linked_path is not None and error is None
    ]
    root_agent_links = set(root_agent_navigation)
    for entrypoint in ROOT_INDEX_FILES:
        if entrypoint not in root_agent_links:
            findings.append(
                ContextFinding(
                    "missing_root_entrypoint",
                    Path("AGENTS.md"),
                    f"root startup index must link to {entrypoint}",
                )
            )
    root_entrypoints = root_entrypoint_targets(
        shared_markdown.get(Path("AGENTS.md"), "")
    )
    resolved_root_entrypoints = tuple(
        _resolve_local_link(repo_root, Path("AGENTS.md"), target)[0]
        if target is not None
        else None
        for target in root_entrypoints[: len(ROOT_INDEX_FILES)]
    )
    if (
        all(entrypoint in root_agent_links for entrypoint in ROOT_INDEX_FILES)
        and resolved_root_entrypoints != ROOT_INDEX_FILES
    ):
        findings.append(
            ContextFinding(
                "misordered_root_entrypoints",
                Path("AGENTS.md"),
                "the first four startup list items must be README, directory, PRD, development",
            )
        )
    module_index_entries = root_module_index_entries(
        shared_markdown.get(Path("AGENTS.md"), "")
    )
    for module_root in MODULE_CONTEXT_ROOTS:
        module_agents = module_root / "AGENTS.md"
        matching_entries: list[tuple[str, tuple[str, ...]]] = []
        for scope, targets in module_index_entries:
            resolved_targets = tuple(
                _resolve_local_link(repo_root, Path("AGENTS.md"), target)[0]
                for target in targets
            )
            if module_agents in resolved_targets:
                matching_entries.append((scope, targets))
        if (
            len(matching_entries) != 1
            or not matching_entries[0][0].strip()
            or len(matching_entries[0][1]) != 1
        ):
            findings.append(
                ContextFinding(
                    "unindexed_module_context",
                    Path("AGENTS.md"),
                    f"domain index needs one scoped row linking to {module_agents}",
                )
            )

    worktree_path = repo_root / ".worktreeinclude"
    if not worktree_path.is_file():
        findings.append(
            ContextFinding(
                "missing_worktree_include",
                Path(".worktreeinclude"),
                "file is missing",
            )
        )
        return tuple(findings)
    worktree_include = worktree_path.read_text(encoding="utf-8")
    include_lines = {
        line.strip()
        for line in worktree_include.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if "CLAUDE.local.md" not in include_lines or "docs/" not in include_lines:
        findings.append(
            ContextFinding(
                "incomplete_private_worktree_copy",
                Path(".worktreeinclude"),
                "private worktrees must copy CLAUDE.local.md and docs/",
            )
        )
    for shared_root in ("AGENTS.md", "CLAUDE.md"):
        if shared_root in include_lines:
            findings.append(
                ContextFinding(
                    "redundant_shared_worktree_copy",
                    Path(".worktreeinclude"),
                    f"tracked shared file must not be copied: {shared_root}",
                )
            )

    snapshot_paths = (
        set(shared_markdown)
        | set(link_paths)
        | set(command_target_paths)
        | set(audit_control_files)
        | ignore_policy_files
    )
    for relative_path in sorted(snapshot_paths & set(special_index_flags)):
        findings.append(
            ContextFinding(
                "special_index_flag",
                relative_path,
                f"strict audit rejects Git index flag {special_index_flags[relative_path]!r}",
            )
        )
    for relative_path in sorted(snapshot_paths & worktree_changed):
        if any(finding.path == relative_path for finding in findings):
            continue
        findings.append(
            ContextFinding(
                "staged_worktree_mismatch",
                relative_path,
                "strict audit requires the Git index and worktree to match",
            )
        )

    return tuple(findings)


def main() -> int:
    """运行共享上下文审计，并以退出码表示是否通过。

    Returns:
        没有审计问题时为 0，存在阻断问题时为 1。
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="被检查仓库的根目录，默认使用当前脚本所在仓库",
    )
    parser.add_argument(
        "--allow-untracked",
        action="store_true",
        help="允许尚未进入索引但已经取消忽略的公共文件，仅供未提交开发树复验",
    )
    args = parser.parse_args()
    findings = audit_repository(
        args.repo_root, require_tracked=not args.allow_untracked
    )
    for finding in findings:
        print(f"{finding.path}: {finding.code}: {finding.detail}")
    print(f"findings={len(findings)}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
