#!/usr/bin/env python3
"""检查公共 Agent 上下文的跟踪、链接和基础隐私边界。"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token


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

MODULE_CONTEXT_ROOTS: tuple[Path, ...] = (Path("trowel_py/discussion"),)

MODULE_CONTEXT_REQUIRED_HEADINGS: dict[Path, tuple[str, ...]] = {
    Path("trowel_py/discussion"): (
        "领域职责与 owner",
        "不可破坏的不变量",
        "Agent runtime、Memory、Profile 与 telemetry 边界",
        "运行事实状态",
        "权威测试入口",
        "继续阅读",
    )
}

MODULE_CONTEXT_REQUIRED_STATUS_GROUPS: dict[Path, tuple[tuple[str, ...], ...]] = {
    Path("trowel_py/discussion"): (
        ("verified_live", "compatibility_required"),
        ("unknown",),
    )
}

ROOT_INDEX_FILES: tuple[Path, ...] = (
    Path("README.md"),
    Path("directory.md"),
    Path("docs/foundation/prd.md"),
    Path("docs/foundation/development.md"),
)

AUDIT_CONTROL_FILES: tuple[Path, ...] = (
    Path(".gitignore"),
    Path(".worktreeinclude"),
    Path("scripts/shared_context_check.py"),
    Path("tests/foundation/test_shared_context.py"),
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

_CLAUDE_IMPORT_PATTERN = re.compile(r"(?:^|\s)@((?:[^\s\\]|\\ )+)")
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
_ALLOWED_RAW_HTML: dict[str, frozenset[str]] = {
    "p": frozenset({"align"}),
    "strong": frozenset(),
    "img": frozenset({"src", "alt", "width"}),
}


@dataclass(frozen=True)
class ContextFinding:
    """记录一项会阻止公共上下文发布的问题。

    Attributes:
        code: 供测试和排障稳定识别的问题类别。
        path: 相对仓库根目录的问题文件或目标路径。
        detail: 面向维护者的具体失败原因。
    """

    code: str
    path: Path
    detail: str


@dataclass(frozen=True)
class MarkdownTarget:
    """记录一个结构化 Markdown 目标及其导航语义。

    Attributes:
        target: Markdown 解析器返回的原始目标。
        is_image: 目标来自图片时为 True，来自可导航链接时为 False。
    """

    target: str
    is_image: bool


class _RawHtmlTargetParser(HTMLParser):
    """从 CommonMark 原始 HTML token 中提取可加载的本地或外部目标。

    Attributes:
        targets: 按 HTML 属性出现顺序保存的结构化目标。
        unsupported: 不属于当前 README 白名单的标签、属性或声明。
        visible_text: HTML 正文和图片替代文字组成的可见文本片段。
        attribute_values: HTMLParser 已完成字符引用解码的全部属性值。
    """

    def __init__(self) -> None:
        """初始化启用字符引用解码的标准库 HTML 解析器。"""

        super().__init__(convert_charrefs=True)
        self.targets: list[MarkdownTarget] = []
        self.unsupported: list[str] = []
        self.visible_text: list[str] = []
        self.attribute_values: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """收集开始标签里的 ``href`` 和 ``src`` 属性。

        Args:
            tag: HTML 标签名；当前策略不按标签白名单过滤。
            attrs: HTMLParser 解析后的属性名和值。
        """

        allowed_attrs = _ALLOWED_RAW_HTML.get(tag)
        if allowed_attrs is None:
            self.unsupported.append(f"tag:{tag}")
            allowed_attrs = frozenset()
        for name, value in attrs:
            if value is not None:
                self.attribute_values.append(value)
            if name not in allowed_attrs:
                self.unsupported.append(f"attribute:{tag}.{name}")
            if name in {"href", "src"} and value:
                self.targets.append(MarkdownTarget(value, is_image=name == "src"))
            if tag == "img" and name == "alt" and value:
                self.visible_text.append(value)

    def handle_endtag(self, tag: str) -> None:
        """拒绝不在公共 README 白名单中的结束标签。

        Args:
            tag: HTML 结束标签名。
        """

        if tag not in _ALLOWED_RAW_HTML:
            self.unsupported.append(f"tag:{tag}")

    def handle_comment(self, data: str) -> None:
        """拒绝公共可达 Markdown 中的 HTML 注释。

        Args:
            data: 注释正文；只用于满足 HTMLParser 回调契约。
        """

        del data
        self.unsupported.append("comment")

    def handle_data(self, data: str) -> None:
        """收集 HTMLParser 已完成字符引用解码的可见正文。

        Args:
            data: 标签之间对读者可见的正文。
        """

        self.visible_text.append(data)

    def handle_decl(self, decl: str) -> None:
        """拒绝公共可达 Markdown 中的 HTML 声明。

        Args:
            decl: 声明正文；只用于诊断类别，不进入输出。
        """

        del decl
        self.unsupported.append("declaration")

    def handle_pi(self, data: str) -> None:
        """拒绝公共可达 Markdown 中的 HTML processing instruction。

        Args:
            data: processing instruction 正文；不进入公共上下文输出。
        """

        del data
        self.unsupported.append("processing-instruction")


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

    paths = tuple(dict.fromkeys(relative_paths))
    if not paths:
        return set()
    input_text = "".join(f"{path}\0" for path in paths)
    completed = subprocess.run(
        (
            "git",
            "-c",
            "core.excludesFile=/dev/null",
            "check-ignore",
            "-v",
            "--stdin",
            "-z",
            "--no-index",
        ),
        cwd=repo_root,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode not in (0, 1):
        raise RuntimeError(f"git check-ignore failed: {completed.stderr.strip()}")
    fields = [value for value in completed.stdout.split("\0") if value]
    ignored: set[Path] = set()
    for index in range(0, len(fields), 4):
        source, _line, pattern, matched_path = fields[index : index + 4]
        source_path = Path(source)
        if (
            source_path.name == ".gitignore"
            and source_path in tracked_files
            and not pattern.startswith("!")
        ):
            ignored.add(Path(matched_path))
    return ignored


def _walk_tokens(tokens: Iterable[Token]) -> Iterable[Token]:
    """递归遍历 Markdown token 及其行内子 token。

    Args:
        tokens: Markdown 解析器产生的当前层 token 序列。

    Yields:
        按文档顺序遍历的当前 token 及全部子 token。
    """

    for token in tokens:
        yield token
        if token.children:
            yield from _walk_tokens(token.children)


def _markdown_targets(markdown: str) -> tuple[MarkdownTarget, ...]:
    """用 CommonMark 解析器返回带导航语义的链接和图片目标。

    Args:
        markdown: 需要解析的公共 Markdown 正文。

    Returns:
        按出现顺序排列的结构化目标。
    """

    parser = MarkdownIt("commonmark")
    # 链接协议由本脚本统一裁决，不能让解析器先静默丢掉 file 等危险协议。
    parser.validateLink = lambda _target: True
    targets: list[MarkdownTarget] = []
    for token in _walk_tokens(parser.parse(markdown)):
        if token.type == "link_open":
            href = token.attrGet("href")
            if isinstance(href, str) and href:
                targets.append(MarkdownTarget(href, is_image=False))
        elif token.type == "image":
            src = token.attrGet("src")
            if isinstance(src, str) and src:
                targets.append(MarkdownTarget(src, is_image=True))
        elif token.type in {"html_block", "html_inline"}:
            html_parser = _RawHtmlTargetParser()
            html_parser.feed(token.content)
            targets.extend(html_parser.targets)
    return tuple(targets)


def markdown_links(markdown: str) -> tuple[str, ...]:
    """返回正文中所有需要做路径和隐私审计的链接及图片目标。

    Args:
        markdown: 需要解析的公共 Markdown 正文。

    Returns:
        按出现顺序排列的链接与图片原始目标。
    """

    return tuple(item.target for item in _markdown_targets(markdown))


def _inline_visible_text(tokens: Iterable[Token]) -> str:
    """把一组 CommonMark 行内 token 投影为连续可见文本。

    Args:
        tokens: 同一行内容器的子 token；强调和链接边界不会打断相邻文本。

    Returns:
        字符引用和反斜杠转义已解码，并包含代码、图片替代文字和允许 HTML 的正文。
    """

    parts: list[str] = []
    parser = MarkdownIt("commonmark")
    for token in tokens:
        if token.type in {"text", "code_inline"}:
            parts.append(token.content)
        elif token.type in {"softbreak", "hardbreak"}:
            parts.append("\n")
        elif token.type == "image":
            inline_tokens = parser.parseInline(token.content)
            for inline in inline_tokens:
                parts.append(_inline_visible_text(inline.children or ()))
        elif token.type == "html_inline":
            html_parser = _RawHtmlTargetParser()
            html_parser.feed(token.content)
            parts.extend(html_parser.visible_text)
    return "".join(parts)


def markdown_visible_text(markdown: str) -> str:
    """返回 CommonMark 解码并去掉展示标记后的完整可见正文。

    Args:
        markdown: 需要投影的公共 Markdown 正文。

    Returns:
        各块以换行分隔的正文，包含行内代码、围栏代码、图片替代文字和 HTML 正文。
    """

    blocks: list[str] = []
    for token in MarkdownIt("commonmark").parse(markdown):
        if token.type == "inline":
            blocks.append(_inline_visible_text(token.children or ()))
        elif token.type in {"fence", "code_block"}:
            blocks.append(token.content)
        elif token.type == "html_block":
            html_parser = _RawHtmlTargetParser()
            html_parser.feed(token.content)
            blocks.append("".join(html_parser.visible_text))
    return "\n".join(blocks)


def markdown_attribute_values(markdown: str) -> tuple[str, ...]:
    """返回 CommonMark 与允许 HTML 已解码的全部结构化属性值。

    Args:
        markdown: 需要提取链接、图片和原始 HTML 属性的公共 Markdown。

    Returns:
        按文档顺序排列的属性值；包含链接与图片 title，以及 HTML 的全部属性。
    """

    values: list[str] = []
    parser = MarkdownIt("commonmark")
    parser.validateLink = lambda _target: True
    for token in _walk_tokens(parser.parse(markdown)):
        if token.type in {"link_open", "image"}:
            values.extend(
                value
                for value in token.attrs.values()
                if isinstance(value, str) and value
            )
        elif token.type in {"html_block", "html_inline"}:
            html_parser = _RawHtmlTargetParser()
            html_parser.feed(token.content)
            values.extend(html_parser.attribute_values)
    return tuple(values)


def markdown_headings(markdown: str) -> tuple[str, ...]:
    """返回 CommonMark 文档中按顺序出现的可见标题正文。

    Args:
        markdown: 需要读取章节结构的公共 Markdown。

    Returns:
        去掉展示标记并完成字符引用解码的标题正文。
    """

    tokens = MarkdownIt("commonmark").parse(markdown)
    headings: list[str] = []
    for index, token in enumerate(tokens[:-1]):
        if token.type != "heading_open" or tokens[index + 1].type != "inline":
            continue
        headings.append(_inline_visible_text(tokens[index + 1].children or ()))
    return tuple(headings)


def markdown_section_table_rows(
    markdown: str, heading_text: str
) -> tuple[tuple[str, ...], ...]:
    """返回指定 H2 章节内首张 Markdown 表格的可见单元格。

    Args:
        markdown: 需要读取结构化表格的公共 Markdown。
        heading_text: 目标二级标题的可见正文。

    Returns:
        包含表头的行序列；章节或表格缺失时返回空元组。
    """

    parser = MarkdownIt("commonmark").enable("table")
    tokens = parser.parse(markdown)
    section_start: int | None = None
    section_end = len(tokens)
    for index, token in enumerate(tokens[:-1]):
        if token.type != "heading_open" or token.tag != "h2":
            continue
        heading = tokens[index + 1]
        if heading.type != "inline":
            continue
        current_heading = _inline_visible_text(heading.children or ())
        if section_start is None and current_heading == heading_text:
            section_start = index + 3
        elif section_start is not None:
            section_end = index
            break
    if section_start is None:
        return ()

    table_start = next(
        (
            index
            for index in range(section_start, section_end)
            if tokens[index].type == "table_open"
        ),
        None,
    )
    if table_start is None:
        return ()

    rows: list[tuple[str, ...]] = []
    current_row: list[str] | None = None
    in_cell = False
    for token in tokens[table_start + 1 : section_end]:
        if token.type == "table_close":
            break
        if token.type == "tr_open":
            current_row = []
        elif token.type == "tr_close":
            if current_row is not None:
                rows.append(tuple(current_row))
            current_row = None
        elif token.type in {"th_open", "td_open"}:
            in_cell = True
        elif token.type in {"th_close", "td_close"}:
            in_cell = False
        elif token.type == "inline" and current_row is not None and in_cell:
            current_row.append(_inline_visible_text(token.children or ()))
    return tuple(rows)


def module_status_evidence_fields(evidence: str) -> dict[str, str] | None:
    """解析运行事实单元格中的分号分隔 ``key=value`` 证据字段。

    Args:
        evidence: 状态表第二列的可见正文。

    Returns:
        字段和值均非空且键不重复时返回映射，格式无效时返回 None。
    """

    fields: dict[str, str] = {}
    for item in evidence.split(";"):
        key, separator, value = item.strip().partition("=")
        if not separator or not key or not value.strip() or key in fields:
            return None
        fields[key] = value.strip()
    return fields


def root_module_index_entries(markdown: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """返回根 ``按领域继续读`` 表格中的任务摘要和公共入口目标。

    Args:
        markdown: 根 ``AGENTS.md`` 正文。

    Returns:
        每个数据行的首列可见任务摘要，以及第二列中的全部导航目标。
    """

    parser = MarkdownIt("commonmark").enable("table")
    tokens = parser.parse(markdown)
    section_start: int | None = None
    section_end = len(tokens)
    for index, token in enumerate(tokens[:-1]):
        if token.type != "heading_open" or token.tag != "h2":
            continue
        heading = tokens[index + 1]
        if heading.type != "inline":
            continue
        text = _inline_visible_text(heading.children or ())
        if section_start is None and text == "按领域继续读":
            section_start = index + 3
        elif section_start is not None:
            section_end = index
            break
    if section_start is None:
        return ()

    table_start = next(
        (
            index
            for index in range(section_start, section_end)
            if tokens[index].type == "table_open"
        ),
        None,
    )
    if table_start is None:
        return ()

    rows: list[tuple[str, tuple[str, ...]]] = []
    current_cells: list[tuple[str, tuple[str, ...]]] | None = None
    in_cell = False
    for token in tokens[table_start + 1 : section_end]:
        if token.type == "table_close":
            break
        if token.type == "tr_open":
            current_cells = []
        elif token.type == "tr_close":
            if current_cells is not None and len(current_cells) >= 2 and rows:
                rows.append((current_cells[0][0], current_cells[1][1]))
            elif current_cells is not None and not rows:
                rows.append(("", ()))
            current_cells = None
        elif token.type in {"th_open", "td_open"}:
            in_cell = True
        elif token.type in {"th_close", "td_close"}:
            in_cell = False
        elif token.type == "inline" and current_cells is not None and in_cell:
            targets = tuple(
                target
                for child in _walk_tokens(token.children or ())
                if child.type == "link_open"
                for target in (child.attrGet("href"),)
                if isinstance(target, str) and target
            )
            current_cells.append((_inline_visible_text(token.children or ()), targets))
    return tuple(rows[1:])


def root_entrypoint_targets(markdown: str) -> tuple[str | None, ...]:
    """返回根 AGENTS ``开工入口`` 章节首个有序列表的逐项链接目标。

    Args:
        markdown: 根 ``AGENTS.md`` 正文。

    Returns:
        每个一级列表项唯一导航链接的目标；无链接或有多个链接时该项为 None。
        章节或有序列表缺失时返回空元组。
    """

    tokens = MarkdownIt("commonmark").parse(markdown)
    section_start: int | None = None
    section_end = len(tokens)
    for index, token in enumerate(tokens[:-1]):
        if token.type != "heading_open" or token.tag != "h2":
            continue
        heading = tokens[index + 1]
        if heading.type != "inline":
            continue
        heading_text = _inline_visible_text(heading.children or ())
        if section_start is None and heading_text == "开工入口":
            section_start = index + 3
        elif section_start is not None:
            section_end = index
            break
    if section_start is None:
        return ()

    list_start = next(
        (
            index
            for index in range(section_start, section_end)
            if tokens[index].type == "ordered_list_open" and tokens[index].level == 0
        ),
        None,
    )
    if list_start is None:
        return ()

    item_targets: list[str | None] = []
    current_targets: list[str] | None = None
    for token in tokens[list_start + 1 : section_end]:
        if token.type == "ordered_list_close" and token.level == 0:
            break
        if token.type == "list_item_open" and token.level == 1:
            current_targets = []
            continue
        if token.type == "list_item_close" and token.level == 1:
            if current_targets is not None:
                item_targets.append(
                    current_targets[0] if len(current_targets) == 1 else None
                )
            current_targets = None
            continue
        if token.type != "inline" or current_targets is None:
            continue
        current_targets.extend(
            target
            for child in _walk_tokens(token.children or ())
            if child.type == "link_open"
            for target in (child.attrGet("href"),)
            if isinstance(target, str) and target
        )
    return tuple(item_targets)


def claude_imports(markdown: str) -> tuple[str, ...]:
    """返回 Claude Code 会从 Markdown 正文解释出的 ``@path`` 导入。

    Args:
        markdown: 需要按 Claude memory import 语义检查的公共正文。

    Returns:
        按出现顺序排列、去掉 fragment 并还原转义空格的导入路径。代码块和行内代码
        不属于 Claude 导入，因此不会返回。
    """

    imports: list[str] = []
    for token in _walk_tokens(MarkdownIt("commonmark").parse(markdown)):
        if token.type != "text":
            continue
        for match in _CLAUDE_IMPORT_PATTERN.finditer(token.content):
            path = match.group(1).split("#", 1)[0].replace(r"\ ", " ")
            if not path:
                continue
            valid = (
                path.startswith("./")
                or path.startswith("~/")
                or (path.startswith("/") and path != "/")
                or (
                    not path.startswith("@")
                    and not re.match(r"^[#%^&*()]+", path)
                    and re.match(r"^[A-Za-z0-9._-]", path) is not None
                )
            )
            if valid:
                imports.append(path)
    return tuple(imports)


def has_raw_html(markdown: str) -> bool:
    """判断公共 Markdown 是否含链接审计无法结构化解释的原始 HTML。"""

    return any(
        token.type in {"html_block", "html_inline"}
        for token in _walk_tokens(MarkdownIt("commonmark").parse(markdown))
    )


def unsupported_raw_html(markdown: str) -> tuple[str, ...]:
    """返回公共 README 白名单无法解释的原始 HTML 结构。"""

    unsupported: list[str] = []
    for token in _walk_tokens(MarkdownIt("commonmark").parse(markdown)):
        if token.type not in {"html_block", "html_inline"}:
            continue
        parser = _RawHtmlTargetParser()
        parser.feed(token.content)
        unsupported.extend(parser.unsupported)
    return tuple(unsupported)


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

    if require_tracked:
        for relative_path in AUDIT_CONTROL_FILES:
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
        for relative_path in sorted(ignore_policy_files - set(AUDIT_CONTROL_FILES)):
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
        for target in _markdown_targets(markdown):
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
    ignored = _ignored_files(
        repo_root,
        (*SHARED_CONTEXT_FILES, *shared_markdown, *link_paths, *private_files),
        tracked,
    )

    bridge_paths = (
        Path("CLAUDE.md"),
        *(module_root / "CLAUDE.md" for module_root in MODULE_CONTEXT_ROOTS),
    )

    invalid_linked_contexts = {
        path
        for path in shared_markdown
        if path not in SHARED_CONTEXT_FILES and (_private_path(path) or path in ignored)
    }

    public_docs = (set(shared_markdown) - invalid_linked_contexts) | {
        path for path in link_paths if not _private_path(path) and path not in ignored
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
        if relative_path in ignored:
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
            elif linked_path in ignored:
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
        if relative_path not in ignored:
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
        status_rows = markdown_section_table_rows(module_markdown, "运行事实状态")
        parsed_statuses: set[str] = set()
        positive_statuses: set[str] = set()
        valid_status_table = bool(
            status_rows
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
        | set(AUDIT_CONTROL_FILES)
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
    print(f"checked={len(SHARED_CONTEXT_FILES)} findings={len(findings)}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
