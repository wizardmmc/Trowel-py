#!/usr/bin/env python3
"""检查公共 Agent 上下文的跟踪、链接和基础隐私边界。"""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token


SHARED_CONTEXT_FILES: tuple[Path, ...] = tuple(
    Path(value)
    for value in (
        "AGENTS.md",
        "CLAUDE.md",
        "directory.md",
        "docs/foundation/prd.md",
        "docs/foundation/development.md",
        "docs/foundation/front-end-design-language.md",
        "docs/reference/agent-runtime.md",
        "docs/reference/memory-runtime.md",
    )
)

PRIVATE_CONTEXT_PATHS: tuple[Path, ...] = tuple(
    Path(value)
    for value in (
        "CLAUDE.local.md",
        "docs/README.md",
        "docs/slices",
        "docs/milestones",
        "docs/design",
        "docs/experiments",
        "docs/archive",
    )
)

_PRIVACY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "absolute_user_path",
        re.compile(r"(?<![A-Za-z0-9])/(?:Users|home)/[^/\s`]+/"),
    ),
    (
        "windows_user_path",
        re.compile(r"(?i)(?<![A-Za-z0-9])[A-Z]:\\Users\\[^\\\s`]+\\"),
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


def _tracked_files(repo_root: Path) -> set[Path]:
    """返回仓库索引中全部已跟踪路径。

    Args:
        repo_root: 需要查询 Git 索引的仓库根目录。

    Returns:
        以仓库根目录为基准的已跟踪路径集合。
    """

    output = _run_git(repo_root, "ls-files", "-z")
    return {Path(value) for value in output.split("\0") if value}


def _ignored_files(repo_root: Path, relative_paths: Iterable[Path]) -> set[Path]:
    """批量返回被当前 Git ignore 规则排除的路径。

    Args:
        repo_root: 需要应用 ignore 规则的 Git 仓库根目录。
        relative_paths: 以仓库根目录为基准的待查路径。

    Returns:
        命中 ignore 规则的仓库相对路径集合。
    """

    paths = tuple(dict.fromkeys(relative_paths))
    if not paths:
        return set()
    input_text = "".join(f"{path}\0" for path in paths)
    completed = subprocess.run(
        ("git", "check-ignore", "--stdin", "-z", "--no-index"),
        cwd=repo_root,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode not in (0, 1):
        raise RuntimeError(f"git check-ignore failed: {completed.stderr.strip()}")
    return {Path(value) for value in completed.stdout.split("\0") if value}


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


def markdown_links(markdown: str) -> tuple[str, ...]:
    """用 CommonMark 解析器返回正文中的链接和图片目标。

    Args:
        markdown: 需要解析的公共 Markdown 正文。

    Returns:
        按出现顺序排列的链接与图片原始目标。
    """

    targets: list[str] = []
    for token in _walk_tokens(MarkdownIt("commonmark").parse(markdown)):
        if token.type == "link_open":
            href = token.attrGet("href")
            if isinstance(href, str) and href:
                targets.append(href)
        elif token.type == "image":
            src = token.attrGet("src")
            if isinstance(src, str) and src:
                targets.append(src)
    return tuple(targets)


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
        relative_path == private_path or private_path in relative_path.parents
        for private_path in PRIVATE_CONTEXT_PATHS
    )


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
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None, None
    decoded_path = unquote(parsed.path)
    candidate = (repo_root / source.parent / decoded_path).resolve()
    try:
        return candidate.relative_to(repo_root.resolve()), None
    except ValueError:
        return None, f"link escapes repository: {target}"


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
    tracked = _tracked_files(repo_root)
    findings: list[ContextFinding] = []
    shared_markdown: dict[Path, str] = {}
    resolved_links: dict[Path, list[tuple[str, Path | None, str | None]]] = {}
    link_paths: list[Path] = []

    for relative_path in SHARED_CONTEXT_FILES:
        absolute_path = repo_root / relative_path
        if not absolute_path.is_file():
            continue
        markdown = absolute_path.read_text(encoding="utf-8")
        shared_markdown[relative_path] = markdown
        resolved_links[relative_path] = []
        for target in markdown_links(markdown):
            linked_path, error = _resolve_local_link(repo_root, relative_path, target)
            resolved_links[relative_path].append((target, linked_path, error))
            if linked_path is not None:
                link_paths.append(linked_path)

    private_files = sorted(
        path.relative_to(repo_root)
        for path in (repo_root / "docs").rglob("*")
        if path.is_file() and path.relative_to(repo_root) not in SHARED_CONTEXT_FILES
    )
    private_files.append(Path("CLAUDE.local.md"))
    ignored = _ignored_files(
        repo_root, (*SHARED_CONTEXT_FILES, *link_paths, *private_files)
    )

    for relative_path in SHARED_CONTEXT_FILES:
        absolute_path = repo_root / relative_path
        if not absolute_path.is_file():
            findings.append(
                ContextFinding("missing_shared_file", relative_path, "file is missing")
            )
            continue
        if require_tracked and relative_path not in tracked:
            findings.append(
                ContextFinding(
                    "untracked_shared_file", relative_path, "file is not tracked by Git"
                )
            )
        if relative_path in ignored:
            findings.append(
                ContextFinding(
                    "ignored_shared_file", relative_path, "file is still ignored"
                )
            )

        markdown = shared_markdown[relative_path]
        for code, matched in privacy_matches(markdown):
            findings.append(
                ContextFinding(code, relative_path, f"matched {matched!r}")
            )
        for target, linked_path, error in resolved_links[relative_path]:
            if error:
                findings.append(ContextFinding("invalid_local_link", relative_path, error))
                continue
            if linked_path is None:
                continue
            if _private_path(linked_path):
                findings.append(
                    ContextFinding(
                        "private_link",
                        relative_path,
                        f"link points to private path: {target}",
                    )
                )
                continue
            if not (repo_root / linked_path).exists():
                findings.append(
                    ContextFinding(
                        "missing_link_target",
                        relative_path,
                        f"link target does not exist: {target}",
                    )
                )
            elif linked_path in ignored:
                findings.append(
                    ContextFinding(
                        "ignored_link_target",
                        relative_path,
                        f"link target is ignored: {target}",
                    )
                )
            elif require_tracked and linked_path not in tracked:
                findings.append(
                    ContextFinding(
                        "untracked_link_target",
                        relative_path,
                        f"link target is not tracked: {target}",
                    )
                )

    for relative_path in private_files:
        if relative_path not in ignored:
            findings.append(
                ContextFinding(
                    "exposed_private_file", relative_path, "private file is not ignored"
                )
            )

    claude_bridge = shared_markdown.get(Path("CLAUDE.md"))
    if claude_bridge is not None and "@AGENTS.md" not in claude_bridge.splitlines():
        findings.append(
            ContextFinding(
                "missing_claude_bridge",
                Path("CLAUDE.md"),
                "CLAUDE.md must import @AGENTS.md on its own line",
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
