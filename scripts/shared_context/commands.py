"""检查权威章节中每行一个、无 shell 控制流的静态验证命令。"""

from __future__ import annotations

import re
import shlex
from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence

from markdown_it import MarkdownIt
from markdown_it.token import Token

from .models import ContextFinding


_UNSUPPORTED_SHELL_BUILTINS = frozenset({"cd", "popd", "pushd"})
_PYTHON_RUNNER_FLAGS = frozenset(
    {"-B", "-E", "-I", "-O", "-OO", "-P", "-q", "-s", "-S", "-u", "-v"}
)


class _BarePathMode(Enum):
    """区分裸路径来自显式路径选项、普通位置参数还是脚本名。"""

    NONE = "none"
    KNOWN_ROOT = "known_root"
    EXPLICIT = "explicit"


class AuthoritativeCommandTargetKind(Enum):
    """声明权威命令目标在真实执行时要求的文件系统类型。"""

    ANY = "any"
    FILE = "file"
    DIRECTORY = "directory"


@dataclass(frozen=True)
class _CommandDialect:
    """声明一种权威命令的静态参数语义。

    Attributes:
        name: 直接可执行文件名或 ``python -m`` 模块末段名称。
        direct_executable: 是否允许该名称作为命令的首个可执行文件。
        script_runner: 第二个位置参数是否只是包脚本名而不是仓库路径。
        flag_options: 不带值且不会引用仓库目标的选项。
        value_options: 带值但该值不是输入路径的选项。
        path_options: 带仓库路径值的选项及其目标类型。
        working_directory_options: 带工作目录值的选项；目录允许尚不存在，供
            freshness 明确报告。
    """

    name: str
    direct_executable: bool = False
    script_runner: bool = False
    flag_options: frozenset[str] = frozenset()
    value_options: frozenset[str] = frozenset()
    path_options: tuple[tuple[str, AuthoritativeCommandTargetKind], ...] = ()
    working_directory_options: frozenset[str] = frozenset()


_COMMAND_DIALECTS: tuple[_CommandDialect, ...] = (
    _CommandDialect(
        "pytest",
        direct_executable=True,
        flag_options=frozenset(
            {"-q", "--quiet", "--strict-config", "--strict-markers"}
        ),
        value_options=frozenset({"-k", "-m", "--junitxml"}),
        path_options=(
            ("-c", AuthoritativeCommandTargetKind.FILE),
            ("--ignore", AuthoritativeCommandTargetKind.ANY),
            ("--rootdir", AuthoritativeCommandTargetKind.DIRECTORY),
        ),
    ),
    _CommandDialect("ruff", direct_executable=True),
    _CommandDialect("mypy", direct_executable=True),
    _CommandDialect(
        "bun",
        direct_executable=True,
        script_runner=True,
        working_directory_options=frozenset({"--cwd"}),
    ),
    _CommandDialect(
        "npm",
        direct_executable=True,
        script_runner=True,
        working_directory_options=frozenset({"--prefix"}),
    ),
    _CommandDialect("pnpm", direct_executable=True, script_runner=True),
    _CommandDialect(
        "yarn",
        direct_executable=True,
        script_runner=True,
        working_directory_options=frozenset({"--cwd"}),
    ),
    _CommandDialect("moon", direct_executable=True),
    _CommandDialect("eslint", direct_executable=True),
    _CommandDialect("public_contracts", flag_options=frozenset({"--update"})),
    _CommandDialect(
        "shared_context_check", flag_options=frozenset({"--allow-untracked"})
    ),
)
_EMPTY_COMMAND_DIALECT = _CommandDialect("")


@dataclass(frozen=True)
class AuthoritativeCommandTarget:
    """记录一条权威命令引用的仓库路径。

    Attributes:
        source: 包含权威命令的共享 Markdown 文件。
        target: 以仓库根目录为基准的文件或目录路径。
        command: 产生该路径引用的原始命令片段，用于失败诊断。
        kind: 真实命令要求目标具备的文件系统类型。
    """

    source: Path
    target: Path
    command: str
    kind: AuthoritativeCommandTargetKind = AuthoritativeCommandTargetKind.ANY


@dataclass(frozen=True)
class AuthoritativeCommandInspection:
    """汇总权威命令中的静态路径和无法可靠解释的语法。

    Attributes:
        targets: 可以确定指向仓库文件或目录的命令引用。
        findings: 需要人工改写成静态入口的不支持语法。
    """

    targets: tuple[AuthoritativeCommandTarget, ...]
    findings: tuple[ContextFinding, ...]


@dataclass(frozen=True)
class _CommandPaths:
    """保存一条命令的静态路径和可选解析失败原因。

    Attributes:
        paths: 从命令中提取出的仓库相对路径及目标类型。
        unsupported_reason: 命令不能确定性解释时的具体原因。
    """

    paths: tuple[_CommandPath, ...]
    unsupported_reason: str | None = None


@dataclass(frozen=True)
class _CommandSection:
    """保存一个权威二级章节的出现次数和首个章节正文。"""

    occurrence_count: int
    fragments: tuple[str, ...]


@dataclass(frozen=True)
class _CommandPath:
    """保存解析出的路径及命令对其要求的文件系统类型。"""

    path: Path
    kind: AuthoritativeCommandTargetKind


def inspect_authoritative_commands(
    markdown_by_path: Mapping[Path, str],
    sections_by_path: Mapping[Path, Sequence[str]],
    *,
    repository_path_roots: Collection[str],
    module_path_roots: Collection[str],
    python_package_roots: Collection[Path] = (),
) -> AuthoritativeCommandInspection:
    """检查权威验证章节，并返回静态路径与不支持语法。

    Args:
        markdown_by_path: 共享 Markdown 路径及当前正文。
        sections_by_path: 每份文档中需要解释为权威命令的二级标题。
        repository_path_roots: 允许命令直接引用的仓库一级目录。
        module_path_roots: 允许 ``python -m`` 映射为仓库文件的一级包名。
        python_package_roots: 仓库中由 ``__init__.py`` 证明存在的包目录。

    Returns:
        按正文顺序去重的路径引用，以及默认失败的不支持语法 finding。
    """

    targets: list[AuthoritativeCommandTarget] = []
    findings: list[ContextFinding] = []
    target_indices: dict[tuple[Path, Path], int] = {}
    for source, section_names in sections_by_path.items():
        markdown = markdown_by_path.get(source)
        if markdown is None:
            continue
        for section_name in section_names:
            section = _section_code_fragments(markdown, section_name)
            if section.occurrence_count == 0:
                findings.append(
                    ContextFinding(
                        "missing_authoritative_command_section",
                        source,
                        f"authoritative command H2 section is missing: {section_name}",
                    )
                )
            elif section.occurrence_count > 1:
                findings.append(
                    ContextFinding(
                        "duplicate_authoritative_command_section",
                        source,
                        f"authoritative command H2 section is duplicated: {section_name}",
                    )
                )
            valid_command_count = 0
            for command in section.fragments:
                parsed = _command_repository_paths(
                    command,
                    repository_path_roots=repository_path_roots,
                    module_path_roots=module_path_roots,
                    python_package_roots=python_package_roots,
                )
                if parsed.unsupported_reason is not None:
                    findings.append(
                        ContextFinding(
                            "unsupported_authoritative_command_syntax",
                            source,
                            f"{parsed.unsupported_reason}: {command}",
                        )
                    )
                else:
                    valid_command_count += 1
                for parsed_target in parsed.paths:
                    key = (source, parsed_target.path)
                    existing_index = target_indices.get(key)
                    if existing_index is not None:
                        existing = targets[existing_index]
                        if (
                            existing.kind is AuthoritativeCommandTargetKind.ANY
                            and parsed_target.kind
                            is not AuthoritativeCommandTargetKind.ANY
                        ):
                            targets[existing_index] = AuthoritativeCommandTarget(
                                existing.source,
                                existing.target,
                                existing.command,
                                parsed_target.kind,
                            )
                        continue
                    target_indices[key] = len(targets)
                    targets.append(
                        AuthoritativeCommandTarget(
                            source,
                            parsed_target.path,
                            command,
                            parsed_target.kind,
                        )
                    )
            if section.occurrence_count > 0 and valid_command_count == 0:
                findings.append(
                    ContextFinding(
                        "empty_authoritative_command_section",
                        source,
                        f"authoritative command H2 has no valid command: {section_name}",
                    )
                )
    return AuthoritativeCommandInspection(tuple(targets), tuple(findings))


def audit_authoritative_command_targets(
    repo_root: Path,
    targets: Sequence[AuthoritativeCommandTarget],
    *,
    tracked_files: Collection[Path] = (),
    ignored_paths: Collection[Path] = (),
    nonregular_tracked_files: Collection[Path] = (),
    require_tracked: bool = False,
) -> tuple[ContextFinding, ...]:
    """报告权威命令中已经不存在的仓库文件或目录。

    Args:
        repo_root: 被检查 Git 仓库的根目录。
        targets: 从权威验证章节提取的仓库路径引用。
        tracked_files: Git 索引中的文件路径；目录由其下已跟踪文件证明可发布。
        ignored_paths: 命中仓库已跟踪 ignore 规则的命令目标。
        nonregular_tracked_files: Git 索引中不是普通 blob 的命令目标。
        require_tracked: 是否要求命令目标能从 Git 索引恢复。

    Returns:
        每个失效引用对应一项可定位到来源文档的阻断问题。
    """

    findings: list[ContextFinding] = []
    resolved_root = repo_root.resolve()
    for target in targets:
        absolute_target = resolved_root / target.target
        target_is_tracked = _tracked_target(target.target, tracked_files)
        try:
            absolute_target.resolve().relative_to(resolved_root)
        except ValueError:
            findings.append(
                ContextFinding(
                    "invalid_authoritative_command_target",
                    target.source,
                    f"command target escapes repository: {target.target}",
                )
            )
            continue
        if _target_has_symlink(resolved_root, target.target):
            findings.append(
                ContextFinding(
                    "symlink_authoritative_command_target",
                    target.source,
                    f"command target must not use symlinks: {target.target} "
                    f"({target.command})",
                )
            )
            continue
        if target.target in nonregular_tracked_files:
            findings.append(
                ContextFinding(
                    "nonregular_authoritative_command_target",
                    target.source,
                    f"tracked command target is not a regular blob: {target.target} "
                    f"({target.command})",
                )
            )
            continue
        if (
            absolute_target.exists()
            and target.kind is AuthoritativeCommandTargetKind.DIRECTORY
            and not absolute_target.is_dir()
        ):
            findings.append(
                ContextFinding(
                    "invalid_authoritative_command_target_type",
                    target.source,
                    f"command target must be a directory: {target.target} "
                    f"({target.command})",
                )
            )
            continue
        if (
            absolute_target.exists()
            and target.kind is AuthoritativeCommandTargetKind.FILE
            and not absolute_target.is_file()
        ):
            findings.append(
                ContextFinding(
                    "invalid_authoritative_command_target_type",
                    target.source,
                    f"command target must be a file: {target.target} ({target.command})",
                )
            )
            continue
        if absolute_target.exists():
            if target.target in ignored_paths and not target_is_tracked:
                findings.append(
                    ContextFinding(
                        "ignored_authoritative_command_target",
                        target.source,
                        f"command target is ignored: {target.target} ({target.command})",
                    )
                )
            elif require_tracked and not target_is_tracked:
                findings.append(
                    ContextFinding(
                        "untracked_authoritative_command_target",
                        target.source,
                        f"command target is not tracked: {target.target} "
                        f"({target.command})",
                    )
                )
            continue
        findings.append(
            ContextFinding(
                "missing_authoritative_command_target",
                target.source,
                f"command target does not exist: {target.target} ({target.command})",
            )
        )
    return tuple(findings)


def _target_has_symlink(repo_root: Path, target: Path) -> bool:
    """判断命令目标或其任一仓库内父路径是否为符号链接。"""

    current = repo_root
    for part in target.parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def _tracked_target(target: Path, tracked_files: Collection[Path]) -> bool:
    """判断文件或目录目标能否从 Git 索引恢复。

    Args:
        target: 权威命令引用的仓库相对路径。
        tracked_files: Git 索引中的文件路径。

    Returns:
        文件本身已跟踪，或目录下至少有一个已跟踪文件时为 True。
    """

    return target in tracked_files or any(
        target in path.parents for path in tracked_files
    )


def _section_code_fragments(markdown: str, section_name: str) -> _CommandSection:
    """返回指定二级章节的出现次数、代码块和行内代码正文。

    Args:
        markdown: 需要解析的 Markdown 正文。
        section_name: 精确匹配的二级标题可见文本。

    Returns:
        章节出现次数，以及首个同名二级章节内按正文顺序排列的代码片段。
    """

    tokens = MarkdownIt("commonmark").parse(markdown)
    headings: list[tuple[int, str]] = []
    for index, token in enumerate(tokens[:-1]):
        if token.type != "heading_open" or token.tag != "h2":
            continue
        heading = tokens[index + 1]
        if heading.type != "inline":
            continue
        headings.append((index, _inline_text(heading.children or ())))
    matching_indices = [index for index, text in headings if text == section_name]
    if not matching_indices:
        return _CommandSection(0, ())
    first_heading_index = matching_indices[0]
    section_start = first_heading_index + 3
    section_end = next(
        (index for index, _text in headings if index > first_heading_index), len(tokens)
    )

    fragments: list[str] = []
    for token in tokens[section_start:section_end]:
        if token.type in {"fence", "code_block"}:
            fragments.extend(
                line.strip()
                for line in token.content.splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            )
        elif token.type == "inline":
            fragments.extend(_inline_command_fragments(token.children or ()))
    return _CommandSection(len(matching_indices), tuple(fragments))


def _inline_command_fragments(tokens: Sequence[Token]) -> tuple[str, ...]:
    """返回独占一行或紧随换行的行内代码命令，忽略正文术语。"""

    fragments: list[str] = []
    for index, token in enumerate(tokens):
        if token.type != "code_inline" or not token.content.strip():
            continue
        is_standalone = len(tokens) == 1
        follows_break = index > 0 and tokens[index - 1].type in {
            "softbreak",
            "hardbreak",
        }
        if is_standalone or follows_break:
            fragments.append(token.content)
    return tuple(fragments)


def _inline_text(tokens: Sequence[Token]) -> str:
    """返回标题行内 token 的连续可见文本。"""

    return "".join(
        token.content for token in tokens if token.type in {"text", "code_inline"}
    )


def _command_repository_paths(
    command: str,
    *,
    repository_path_roots: Collection[str],
    module_path_roots: Collection[str],
    python_package_roots: Collection[Path],
) -> _CommandPaths:
    """从一个 shell 命令片段中提取可确定的仓库路径。

    Args:
        command: Markdown 代码块的一行或单个行内代码片段。
        repository_path_roots: 允许直接引用的仓库一级目录。
        module_path_roots: 允许映射的仓库 Python 一级包名。
        python_package_roots: 仓库中由 ``__init__.py`` 证明存在的包目录。

    Returns:
        静态仓库路径，以及无法确定性解释时的失败原因。
    """

    tokens, token_error = _shell_tokens(command)
    if token_error is not None:
        return _CommandPaths((), f"shell syntax cannot be parsed ({token_error})")
    if not tokens:
        return _CommandPaths((), "empty or comment-only commands are not supported")
    if tokens[0] == "." or (tokens[0].startswith("./") and not Path(tokens[0]).parts):
        return _CommandPaths((), "direct script command requires a file path")
    if tokens and all(character in "();&|<>" for character in tokens[0]):
        return _CommandPaths((), "shell control flow and redirection are not supported")
    if tokens and not _supported_command_executable(tokens[0]):
        return _CommandPaths(
            (), "unsupported command executable; use a direct static command"
        )
    if _uses_embedded_command(tokens):
        return _CommandPaths((), "embedded shell or Python commands are not supported")
    python_module_index, python_error = _python_module_index(tokens)
    if python_error is not None:
        return _CommandPaths((), python_error)
    command_name = _command_name(tokens, python_module_index)
    dialect = _command_dialect(command_name)
    directory_option_error = _directory_option_error(
        tokens, dialect.working_directory_options
    )
    if directory_option_error is not None:
        return _CommandPaths((), directory_option_error)
    path_options = dict(dialect.path_options)
    paths: list[_CommandPath] = []
    for index, token in enumerate(tokens):
        if token and all(character in "();&|<>" for character in token):
            return _CommandPaths(
                (), "shell control flow and redirection are not supported"
            )
        if index == 0 and token in _UNSUPPORTED_SHELL_BUILTINS:
            return _CommandPaths(
                (),
                "directory-changing shell builtins are not supported; use a cwd option",
            )
        previous = tokens[index - 1] if index else ""
        option_name, separator, option_value = token.partition("=")
        value = token
        target_kind = AuthoritativeCommandTargetKind.ANY
        if index == python_module_index:
            if not _valid_python_module_name(token):
                return _CommandPaths((), f"invalid Python module name: {token}")
            module_path = _repository_module_path(
                token, module_path_roots, python_package_roots
            )
            if module_path is not None:
                paths.append(
                    _CommandPath(module_path, AuthoritativeCommandTargetKind.FILE)
                )
            continue
        if (
            python_module_index is not None
            and index < python_module_index
            and (token == "-m" or token in _PYTHON_RUNNER_FLAGS)
        ):
            continue
        if previous in dialect.value_options:
            continue
        if separator and token.startswith("-"):
            if option_name in dialect.working_directory_options:
                value = option_value
                target_kind = AuthoritativeCommandTargetKind.DIRECTORY
            elif option_name in dialect.value_options:
                if not option_value:
                    return _CommandPaths(
                        (), f"command option requires a value: {option_name}"
                    )
                continue
            elif option_name in path_options:
                if not option_value:
                    return _CommandPaths(
                        (), f"command path option requires a value: {option_name}"
                    )
                value = option_value
                target_kind = path_options[option_name]
            else:
                return _CommandPaths(
                    (), f"unsupported command option semantics: {option_name}"
                )
        elif token in dialect.working_directory_options:
            continue
        elif token in dialect.value_options or token in path_options:
            if index + 1 >= len(tokens) or tokens[index + 1].startswith("-"):
                return _CommandPaths((), f"command option requires a value: {token}")
            continue
        elif token.startswith("-"):
            if token in dialect.flag_options:
                continue
            return _CommandPaths((), f"unsupported command option semantics: {token}")
        elif previous in dialect.working_directory_options:
            target_kind = AuthoritativeCommandTargetKind.DIRECTORY
        elif previous in path_options:
            target_kind = path_options[previous]
        is_positional_argument = (
            index > 0
            and not (separator and token.startswith("-"))
            and previous not in dialect.working_directory_options
            and previous not in path_options
            and previous not in dialect.value_options
        )
        if command_name == "pytest" and is_positional_argument:
            value, node_error = _pytest_path_value(value)
            if node_error is not None:
                return _CommandPaths((), node_error)
        unsupported_reason = _unsupported_token_reason(value, previous=previous)
        if unsupported_reason is not None:
            return _CommandPaths((), unsupported_reason)
        path = _repository_path(
            value,
            bare_path_mode=(
                _BarePathMode.EXPLICIT
                if target_kind is not AuthoritativeCommandTargetKind.ANY
                else _bare_repository_path_mode(tokens, index, dialect)
            ),
            repository_path_roots=repository_path_roots,
        )
        if path is not None:
            paths.append(
                _CommandPath(
                    path,
                    (
                        AuthoritativeCommandTargetKind.FILE
                        if index == 0 and token.startswith("./")
                        else target_kind
                    ),
                )
            )
    unique_paths: dict[Path, _CommandPath] = {}
    for parsed_path in paths:
        existing = unique_paths.get(parsed_path.path)
        if existing is None or (
            existing.kind is AuthoritativeCommandTargetKind.ANY
            and parsed_path.kind is not AuthoritativeCommandTargetKind.ANY
        ):
            unique_paths[parsed_path.path] = parsed_path
    return _CommandPaths(tuple(unique_paths.values()))


def _valid_python_module_name(module: str) -> bool:
    """判断 ``python -m`` 参数是否由非空 Python identifier 组成。"""

    return bool(module) and all(part.isidentifier() for part in module.split("."))


def _pytest_path_value(value: str) -> tuple[str, str | None]:
    """把 pytest node ID 收窄为需要做 freshness 的基础文件路径。"""

    if "::" not in value:
        return value, None
    path, _separator, selector = value.partition("::")
    if not path or not selector:
        return "", f"invalid pytest node ID: {value}"
    return path, None


def _directory_option_error(
    tokens: Sequence[str], working_directory_options: Collection[str]
) -> str | None:
    """返回已登记工作目录选项缺值或使用未支持紧凑写法时的错误。

    Args:
        tokens: 一条命令经 shell 词法分析得到的全部 token。
        working_directory_options: 当前命令方言支持的工作目录选项。

    Returns:
        第一个确定性错误；目录选项完整时返回 None。
    """

    for index, token in enumerate(tokens):
        option_name, separator, value = token.partition("=")
        if separator and option_name in working_directory_options:
            if not value:
                return f"directory option requires a non-empty path: {option_name}"
            continue
        if token in working_directory_options:
            if index + 1 >= len(tokens) or not tokens[index + 1]:
                return f"directory option requires a path: {token}"
            if tokens[index + 1].startswith("-"):
                return f"directory option requires a path before: {tokens[index + 1]}"
            continue
        if token == "-C" or token.startswith("-C="):
            return f"unsupported directory option form: {token}"
    return None


def _supported_command_executable(token: str) -> bool:
    """判断首 token 是否属于权威章节允许的直接静态命令。"""

    executable = Path(token).name
    return (
        token.startswith("./")
        or _is_python_executable(executable)
        or _command_dialect(executable).direct_executable
        or executable in _UNSUPPORTED_SHELL_BUILTINS
    )


def _command_dialect(command_name: str) -> _CommandDialect:
    """返回命令名对应的声明式方言，未知命令使用无选项语义。

    Args:
        command_name: 直接可执行文件名或 Python 模块末段名称。

    Returns:
        已登记的冻结方言；Python 模块等未知名称返回空方言。
    """

    return next(
        (dialect for dialect in _COMMAND_DIALECTS if dialect.name == command_name),
        _EMPTY_COMMAND_DIALECT,
    )


def _is_python_executable(executable: str) -> bool:
    """只接受 Python 3 的标准命令名和数字版本后缀。"""

    return re.fullmatch(r"python(?:3(?:\.\d+)*)?", executable) is not None


def _python_module_index(
    tokens: Sequence[str],
) -> tuple[int | None, str | None]:
    """返回直接 Python runner 的 ``-m`` 模块位置和解析失败原因。"""

    if not tokens or not _is_python_executable(Path(tokens[0]).name):
        return None, None
    for index, token in enumerate(tokens[1:], start=1):
        if token == "-m":
            if index + 1 >= len(tokens):
                return None, "python -m requires a static module name"
            return index + 1, None
        if token in _PYTHON_RUNNER_FLAGS:
            continue
        if token.startswith("-"):
            return None, f"unsupported Python runner option before module: {token}"
        break
    return None, None


def _command_name(tokens: Sequence[str], python_module_index: int | None) -> str:
    """返回决定后续参数语义的实际命令名。"""

    if not tokens:
        return ""
    if python_module_index is not None:
        return tokens[python_module_index].rsplit(".", 1)[-1]
    return Path(tokens[0]).name


def _uses_embedded_command(tokens: Sequence[str]) -> bool:
    """判断命令是否通过 ``-c`` 隐藏另一条无法继续静态审计的命令。"""

    if not tokens:
        return False
    executable = Path(tokens[0]).name
    if not _is_python_executable(executable):
        return False
    for token in tokens[1:]:
        if token == "-m":
            return False
        if token == "-c":
            return True
        if token in _PYTHON_RUNNER_FLAGS:
            continue
        if not token.startswith("-"):
            return False
    return False


def _bare_repository_path_mode(
    tokens: Sequence[str], index: int, dialect: _CommandDialect
) -> _BarePathMode:
    """判断当前位置的裸目录名应采用哪种仓库路径解释方式。

    Args:
        tokens: 一条命令经 shell 词法分析得到的全部 token。
        index: 当前 token 在命令中的位置。
        dialect: 当前命令的声明式参数语义。

    Returns:
        显式工作目录选项的值不要求预先存在；脚本运行器的脚本名不作为路径解释；
        其他命令的位置参数只允许引用已知仓库一级目录。
    """

    if tokens[index].startswith("./"):
        return _BarePathMode.EXPLICIT
    if index == 0:
        return _BarePathMode.NONE
    if tokens[index - 1] in dialect.working_directory_options:
        return _BarePathMode.EXPLICIT
    option_name, separator, _value = tokens[index].partition("=")
    if separator and option_name in dialect.working_directory_options:
        return _BarePathMode.EXPLICIT
    invokes_package_script = dialect.script_runner and len(tokens) > 1 and tokens[1] == "run"
    return _BarePathMode.NONE if invokes_package_script else _BarePathMode.KNOWN_ROOT


def _shell_tokens(command: str) -> tuple[tuple[str, ...], str | None]:
    """用标准 shell 词法规则切分命令，并保留无法解析的原因。"""

    if "\\" in command:
        return (), "backslash escapes and line continuations are not supported"

    lexer = shlex.shlex(command, posix=True, punctuation_chars="();&|<>")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        return tuple(lexer), None
    except ValueError as exc:
        return (), str(exc)


def _unsupported_token_reason(value: str, *, previous: str) -> str | None:
    """返回权威命令 token 无法静态核实的原因。

    Args:
        value: 已去掉 ``--option=`` 前缀的 shell token 值。
        previous: 当前 token 前一个 shell token。

    Returns:
        动态、绝对或越界路径的失败原因；静态 token 返回 None。
    """

    if any(marker in value for marker in ("$", "{", "}", "*", "?", "[", "]", "`")):
        return "dynamic variables and glob expressions are not supported"
    if value.startswith(("/", "~")):
        return "absolute and home-relative paths are not supported"
    if ".." in Path(value).parts:
        return "parent-directory command targets are not supported"
    return None


def _repository_path(
    value: str,
    *,
    bare_path_mode: _BarePathMode,
    repository_path_roots: Collection[str],
) -> Path | None:
    """把命令 token 解释为受控仓库路径。

    Args:
        value: shell 词法分析后的单个 token。
        bare_path_mode: 当前命令参数解释裸仓库路径的规则。
        repository_path_roots: 由仓库审计编排器注入的一级目录。

    Returns:
        可确定属于仓库的相对路径；外部命令、虚拟环境和动态表达式返回 None。
    """

    if not value or any(marker in value for marker in ("*", "$", "{", "}")):
        return None
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        return None
    first = candidate.parts[0]
    if first == ".venv":
        return None
    if len(candidate.parts) == 1:
        if bare_path_mode is _BarePathMode.EXPLICIT:
            return candidate
        if bare_path_mode is _BarePathMode.KNOWN_ROOT:
            return (
                candidate
                if first in repository_path_roots or bool(candidate.suffix)
                else None
            )
        return None
    return candidate if first in repository_path_roots else None


def _repository_module_path(
    module: str,
    module_path_roots: Collection[str],
    python_package_roots: Collection[Path],
) -> Path | None:
    """把受控 Python ``-m`` 模块名转换为仓库内文件路径。

    Args:
        module: ``python -m`` 后的模块名。
        module_path_roots: 由仓库审计编排器注入的一级包名。
        python_package_roots: 仓库中由 ``__init__.py`` 证明存在的包目录。

    Returns:
        属于仓库包的 Python 文件路径；外部模块返回 None。
    """

    parts = module.split(".")
    if not parts or parts[0] not in module_path_roots:
        return None
    module_path = Path(*parts)
    if module_path in python_package_roots:
        return module_path / "__main__.py"
    return module_path.with_suffix(".py")
