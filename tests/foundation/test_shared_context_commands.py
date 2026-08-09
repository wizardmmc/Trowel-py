"""验证公共上下文中权威命令引用的提取和 freshness 检查。"""

from pathlib import Path

import pytest

from scripts.shared_context.commands import (
    AuthoritativeCommandTarget,
    audit_authoritative_command_targets,
    inspect_authoritative_commands,
)


def test_authoritative_command_targets_only_read_configured_sections() -> None:
    """命令提取只解释配置章节，并识别模块、文件和显式工作目录。"""

    source = Path("AGENTS.md")
    markdown = """# Public

## 常用验证

```bash
.venv/bin/python -m tests.contracts.public_contracts --update
bun run --cwd web test
bun run --cwd web build
```

`.venv/bin/python -m pytest tests/foundation/test_shared_context.py`

## 普通说明

`.venv/bin/python -m pytest tests/ignored.py`
"""

    inspection = inspect_authoritative_commands(
        {source: markdown},
        {source: ("常用验证",)},
        repository_path_roots={"build", "tests", "web"},
        module_path_roots={"tests"},
    )

    assert inspection.findings == ()
    assert [(target.source, target.target) for target in inspection.targets] == [
        (source, Path("tests/contracts/public_contracts.py")),
        (source, Path("web")),
        (source, Path("tests/foundation/test_shared_context.py")),
    ]


def test_authoritative_section_ignores_inline_terms_before_command_line() -> None:
    """权威章节正文中的行内术语不能被误当成可执行命令。"""

    source = Path("AGENTS.md")
    markdown = """## 常用验证

- 恢复稳定的 `owner_ref`：
  `.venv/bin/python -m pytest tests/real.py`
"""

    inspection = inspect_authoritative_commands(
        {source: markdown},
        {source: ("常用验证",)},
        repository_path_roots={"tests"},
        module_path_roots={"tests"},
    )

    assert inspection.findings == ()
    assert [target.target for target in inspection.targets] == [Path("tests/real.py")]


def test_authoritative_command_inspection_recognizes_bare_repository_directory() -> (
    None
):
    """普通命令参数可以引用无斜杠的仓库一级目录。"""

    source = Path("AGENTS.md")
    inspection = inspect_authoritative_commands(
        {source: "## 常用验证\n\n`pytest tests`\n"},
        {source: ("常用验证",)},
        repository_path_roots={"tests"},
        module_path_roots={"tests"},
    )

    assert inspection.findings == ()
    assert [target.target for target in inspection.targets] == [Path("tests")]


def test_authoritative_command_inspection_recognizes_root_file_arguments(
    tmp_path: Path,
) -> None:
    """带扩展名的根级位置参数按仓库文件审计，即使目标尚不存在。

    Args:
        tmp_path: Pytest 提供的隔离仓库根目录。
    """

    source = Path("AGENTS.md")
    inspection = inspect_authoritative_commands(
        {source: "## 常用验证\n\n`pytest pyproject.toml missing-root.py`\n"},
        {source: ("常用验证",)},
        repository_path_roots={"tests"},
        module_path_roots={"tests"},
    )

    assert inspection.findings == ()
    assert [target.target for target in inspection.targets] == [
        Path("pyproject.toml"),
        Path("missing-root.py"),
    ]
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    findings = audit_authoritative_command_targets(tmp_path, inspection.targets)
    assert [finding.code for finding in findings] == [
        "missing_authoritative_command_target"
    ]


def test_authoritative_command_inspection_recognizes_explicit_cwd_and_executable() -> (
    None
):
    """显式 cwd 可指向尚不存在的目录，仓库脚本本身也必须进入检查。"""

    source = Path("AGENTS.md")
    markdown = """## 常用验证

```bash
bun run --cwd=missing typecheck
bun run --cwd=./also-missing test
bun run --cwd=.//normalized-missing test
./scripts/missing.py
././scripts/normalized-missing.py
./check.py
```
"""

    inspection = inspect_authoritative_commands(
        {source: markdown},
        {source: ("常用验证",)},
        repository_path_roots={"scripts", "tests", "web"},
        module_path_roots={"tests"},
    )

    assert inspection.findings == ()
    assert [target.target for target in inspection.targets] == [
        Path("missing"),
        Path("also-missing"),
        Path("normalized-missing"),
        Path("scripts/missing.py"),
        Path("scripts/normalized-missing.py"),
        Path("check.py"),
    ]


def test_command_argument_semantics_distinguish_modules_selectors_and_paths() -> None:
    """Python 模块、pytest 选择器和普通测试路径不能共享同一个 ``-m`` 猜测。"""

    source = Path("AGENTS.md")
    markdown = """## 常用验证

```bash
pytest -m tests -k tests/missing.py tests/real.py
python -m scripts
python -m scripts.shared_context_check
```
"""

    inspection = inspect_authoritative_commands(
        {source: markdown},
        {source: ("常用验证",)},
        repository_path_roots={"scripts", "tests"},
        module_path_roots={"scripts", "tests"},
        python_package_roots={Path("scripts")},
    )

    assert inspection.findings == ()
    assert [target.target for target in inspection.targets] == [
        Path("tests/real.py"),
        Path("scripts/__main__.py"),
        Path("scripts/shared_context_check.py"),
    ]


def test_pytest_node_ids_audit_the_base_test_file() -> None:
    """pytest 函数、类和参数化 node ID 只把基础文件加入 freshness。"""

    source = Path("AGENTS.md")
    markdown = """## 常用验证

```bash
pytest tests/test_one.py::test_case
python -m pytest tests/test_two.py::TestGroup::test_case[param]
```
"""

    inspection = inspect_authoritative_commands(
        {source: markdown},
        {source: ("常用验证",)},
        repository_path_roots={"tests"},
        module_path_roots={"tests"},
    )

    assert inspection.findings == ()
    assert [target.target for target in inspection.targets] == [
        Path("tests/test_one.py"),
        Path("tests/test_two.py"),
    ]


def test_command_option_schema_separates_outputs_and_input_paths() -> None:
    """pytest 输出选项不做 freshness，声明的输入路径选项保留目标类型。"""

    source = Path("AGENTS.md")
    markdown = """## 常用验证

```bash
pytest --junitxml=report.xml tests/real.py
pytest --rootdir=missing-root tests/real.py
pytest --ignore=tests/ignored.py tests
```
"""

    inspection = inspect_authoritative_commands(
        {source: markdown},
        {source: ("常用验证",)},
        repository_path_roots={"tests"},
        module_path_roots={"tests"},
    )

    assert inspection.findings == ()
    assert [target.target for target in inspection.targets] == [
        Path("tests/real.py"),
        Path("missing-root"),
        Path("tests/ignored.py"),
        Path("tests"),
    ]


def test_command_dialects_accept_moon_and_eslint_without_parser_changes() -> None:
    """新增工具只需登记命令方言，不应继续扩充分支解析器。"""

    source = Path("AGENTS.md")
    markdown = """## 常用验证

```bash
moon run :check
eslint web/src
```
"""

    inspection = inspect_authoritative_commands(
        {source: markdown},
        {source: ("常用验证",)},
        repository_path_roots={"web"},
        module_path_roots=(),
    )

    assert inspection.findings == ()
    assert [target.target for target in inspection.targets] == [Path("web/src")]


def test_pytest_path_options_do_not_apply_node_id_semantics() -> None:
    """pytest 只有位置参数是 node ID，路径选项值必须保持原样。"""

    source = Path("AGENTS.md")
    inspection = inspect_authoritative_commands(
        {
            source: (
                "## 常用验证\n\n"
                "`pytest --ignore=tests/ignored.py::test_case tests/real.py`\n"
            )
        },
        {source: ("常用验证",)},
        repository_path_roots={"tests"},
        module_path_roots=(),
    )

    assert inspection.findings == ()
    assert [target.target for target in inspection.targets] == [
        Path("tests/ignored.py::test_case"),
        Path("tests/real.py"),
    ]


def test_python_module_runner_leaves_later_c_option_to_pytest() -> None:
    """模块选择后的 ``-c`` 属于 pytest 配置路径，不是 Python 内嵌代码。"""

    source = Path("AGENTS.md")
    inspection = inspect_authoritative_commands(
        {source: "## 常用验证\n\n`python -m pytest -c pyproject.toml tests`\n"},
        {source: ("常用验证",)},
        repository_path_roots={"tests"},
        module_path_roots={"tests"},
    )

    assert inspection.findings == ()
    assert [target.target for target in inspection.targets] == [
        Path("pyproject.toml"),
        Path("tests"),
    ]


def test_python_runner_options_and_nested_packages_keep_module_targets() -> None:
    """Python 隔离选项不隐藏 ``-m``，多级包按 ``__main__.py`` 检查。"""

    source = Path("AGENTS.md")
    markdown = """## 常用验证

```bash
python -I -m scripts.missing
python -I -m scripts.shared_context
```
"""

    inspection = inspect_authoritative_commands(
        {source: markdown},
        {source: ("常用验证",)},
        repository_path_roots={"scripts"},
        module_path_roots={"scripts"},
        python_package_roots={Path("scripts/shared_context")},
    )

    assert inspection.findings == ()
    assert [target.target for target in inspection.targets] == [
        Path("scripts/missing.py"),
        Path("scripts/shared_context/__main__.py"),
    ]


@pytest.mark.parametrize(
    ("markdown", "code"),
    (
        ("# Public\n", "missing_authoritative_command_section"),
        ("## 常用验证\n", "empty_authoritative_command_section"),
        (
            "## 常用验证\n\n```bash\n# explanation\n```\n",
            "empty_authoritative_command_section",
        ),
        (
            "## 常用验证\n\n`pytest tests`\n\n## 常用验证\n\n`pytest tests`\n",
            "duplicate_authoritative_command_section",
        ),
    ),
)
def test_authoritative_command_inspection_requires_one_configured_h2(
    markdown: str, code: str
) -> None:
    """权威命令二级章节缺失或重复时必须显式失败。

    Args:
        markdown: 缺失或重复权威章节的测试正文。
        code: 预期的稳定 finding 类别。
    """

    source = Path("AGENTS.md")
    inspection = inspect_authoritative_commands(
        {source: markdown},
        {source: ("常用验证",)},
        repository_path_roots={"tests"},
        module_path_roots={"tests"},
    )

    assert code in {finding.code for finding in inspection.findings}


@pytest.mark.parametrize(
    ("command", "reason"),
    (
        ("pytest $CHECK_PATH", "dynamic variables"),
        ("pytest tests/*.py", "glob expressions"),
        ("pytest tests/../private.py", "parent-directory"),
        ("/usr/bin/pytest tests", "absolute and home-relative"),
        ("pytest tests > report.txt", "redirection"),
        ("pytest &>report.txt tests", "redirection"),
        ("pytest <<< input tests", "redirection"),
        ("(pytest tests)", "shell control flow"),
        ("./", "requires a file path"),
        ("pytest `find tests`", "dynamic variables"),
        ("bash -c 'pytest tests/missing.py'", "unsupported command executable"),
        ("bash scripts/verify.sh", "unsupported command executable"),
        ("sh scripts/verify.sh", "unsupported command executable"),
        ("zsh scripts/verify.sh", "unsupported command executable"),
        ("python -c 'import tests'", "embedded shell"),
        ("python -I -c 'import tests'", "embedded shell"),
        ("env bash -c 'pytest tests/missing.py'", "unsupported command executable"),
        ("uv run python -c 'import tests'", "unsupported command executable"),
        ("sudo pytest tests", "unsupported command executable"),
        ("python-malicious -m tests.missing", "unsupported command executable"),
        ("python-wrapper -m tests.missing", "unsupported command executable"),
        ("nohup python -m scripts.missing", "unsupported command executable"),
        ("timeout 10 pytest tests", "unsupported command executable"),
        ("nice pytest tests", "unsupported command executable"),
        ("doas pytest tests", "unsupported command executable"),
        ("exec pytest tests", "unsupported command executable"),
        (
            "PYTHONPATH=tools python -m scripts.missing",
            "unsupported command executable",
        ),
        ("python -m scripts..missing", "invalid Python module name"),
        ("python -m scripts/missing", "invalid Python module name"),
        ("pytest tests/test_one.py::", "invalid pytest node ID"),
        ("pytest --output=report.xml tests", "unsupported command option"),
        ("pytest --config=config.toml tests", "unsupported command option"),
        ("pytest --output report.xml tests", "unsupported command option"),
        (r"pytest tests\ escaped.py", "backslash"),
        ("bun run --cwd", "directory option requires"),
        ("bun run --cwd=", "directory option requires"),
        ("bun run -C missing test", "unsupported directory option"),
        ("bun run -C=missing test", "unsupported directory option"),
        ("cd web && pytest tests/missing.py", "directory-changing"),
        ("pushd web", "directory-changing"),
        ("popd", "directory-changing"),
        ('pytest "tests', "cannot be parsed"),
    ),
)
def test_authoritative_command_inspection_rejects_uncertain_shell_syntax(
    command: str, reason: str
) -> None:
    """动态或无法确定性解释的权威命令必须默认失败。

    Args:
        command: 放入权威章节的不可静态核实命令。
        reason: 预期诊断中稳定出现的原因片段。
    """

    source = Path("AGENTS.md")
    inspection = inspect_authoritative_commands(
        {source: f"## 常用验证\n\n```bash\n{command}\n```\n"},
        {source: ("常用验证",)},
        repository_path_roots={"tests"},
        module_path_roots={"tests"},
    )

    assert inspection.targets == ()
    syntax_findings = [
        finding
        for finding in inspection.findings
        if finding.code == "unsupported_authoritative_command_syntax"
    ]
    assert len(syntax_findings) == 1
    assert reason in syntax_findings[0].detail


def test_explicit_cwd_targets_keep_missing_ignored_and_untracked_states(
    tmp_path: Path,
) -> None:
    """显式 cwd 目标仍由统一审计器区分缺失、忽略和未跟踪。

    Args:
        tmp_path: Pytest 提供的隔离仓库根目录。
    """

    (tmp_path / "ignored").mkdir()
    (tmp_path / "untracked").mkdir()
    source = Path("AGENTS.md")
    markdown = """## 常用验证

```bash
bun run --cwd=missing test
bun run --cwd=ignored test
bun run --cwd=untracked test
```
"""
    inspection = inspect_authoritative_commands(
        {source: markdown},
        {source: ("常用验证",)},
        repository_path_roots=(),
        module_path_roots=(),
    )

    findings = audit_authoritative_command_targets(
        tmp_path,
        inspection.targets,
        tracked_files=(),
        ignored_paths={Path("ignored")},
        require_tracked=True,
    )

    assert {finding.code for finding in findings} == {
        "missing_authoritative_command_target",
        "ignored_authoritative_command_target",
        "untracked_authoritative_command_target",
    }


def test_explicit_cwd_requires_an_existing_directory(tmp_path: Path) -> None:
    """工作目录选项指向普通文件时必须报告目标类型错误。

    Args:
        tmp_path: Pytest 提供的隔离仓库根目录。
    """

    (tmp_path / "AGENTS.md").write_text("# file\n", encoding="utf-8")
    source = Path("AGENTS.md")
    inspection = inspect_authoritative_commands(
        {source: "## 常用验证\n\n`bun run --cwd=AGENTS.md test`\n"},
        {source: ("常用验证",)},
        repository_path_roots=(),
        module_path_roots=(),
    )

    findings = audit_authoritative_command_targets(tmp_path, inspection.targets)

    assert [finding.code for finding in findings] == [
        "invalid_authoritative_command_target_type"
    ]


def test_direct_script_command_requires_an_existing_file(tmp_path: Path) -> None:
    """首 token 的相对脚本路径不能由同名目录冒充。

    Args:
        tmp_path: Pytest 提供的隔离仓库根目录。
    """

    (tmp_path / "scripts/check").mkdir(parents=True)
    source = Path("AGENTS.md")
    inspection = inspect_authoritative_commands(
        {source: "## 常用验证\n\n`./scripts/check`\n"},
        {source: ("常用验证",)},
        repository_path_roots={"scripts"},
        module_path_roots=(),
    )

    findings = audit_authoritative_command_targets(tmp_path, inspection.targets)

    assert [finding.code for finding in findings] == [
        "invalid_authoritative_command_target_type"
    ]


def test_audit_authoritative_command_targets_reports_only_missing_paths(
    tmp_path: Path,
) -> None:
    """存在的命令目标保持安静，失效目标返回来源和原始命令。

    Args:
        tmp_path: Pytest 提供的隔离仓库根目录。
    """

    existing = Path("tests/existing.py")
    (tmp_path / existing).parent.mkdir(parents=True)
    (tmp_path / existing).write_text("# test\n", encoding="utf-8")
    source = Path("AGENTS.md")
    targets = (
        AuthoritativeCommandTarget(source, existing, "pytest tests/existing.py"),
        AuthoritativeCommandTarget(
            source, Path("tests/missing.py"), "pytest tests/missing.py"
        ),
    )

    findings = audit_authoritative_command_targets(tmp_path, targets)

    assert [(finding.code, finding.path, finding.detail) for finding in findings] == [
        (
            "missing_authoritative_command_target",
            source,
            "command target does not exist: tests/missing.py (pytest tests/missing.py)",
        )
    ]


def test_tracked_target_is_publishable_even_when_a_later_ignore_rule_matches(
    tmp_path: Path,
) -> None:
    """已跟踪目标能从 checkout 恢复，不能被后加 ignore 规则误判。

    Args:
        tmp_path: Pytest 提供的隔离仓库根目录。
    """

    target_path = Path("scripts/tracked.py")
    (tmp_path / target_path).parent.mkdir(parents=True)
    (tmp_path / target_path).write_text("# tracked\n", encoding="utf-8")
    target = AuthoritativeCommandTarget(
        Path("AGENTS.md"), target_path, "python scripts/tracked.py"
    )

    findings = audit_authoritative_command_targets(
        tmp_path,
        (target,),
        tracked_files={target_path},
        ignored_paths={target_path},
        require_tracked=True,
    )

    assert findings == ()


def test_audit_rejects_command_target_symlink_to_private_file(tmp_path: Path) -> None:
    """已跟踪入口也不能通过符号链接依赖 ignored 私人文件。

    Args:
        tmp_path: Pytest 提供的隔离仓库根目录。
    """

    private_path = tmp_path / "docs/slices/private.py"
    private_path.parent.mkdir(parents=True)
    private_path.write_text("# private\n", encoding="utf-8")
    target_path = Path("scripts/check.py")
    (tmp_path / target_path).parent.mkdir(parents=True)
    (tmp_path / target_path).symlink_to("../docs/slices/private.py")
    target = AuthoritativeCommandTarget(
        Path("AGENTS.md"), target_path, "python scripts/check.py"
    )

    findings = audit_authoritative_command_targets(
        tmp_path,
        (target,),
        tracked_files={target_path},
        require_tracked=True,
    )

    assert [finding.code for finding in findings] == [
        "symlink_authoritative_command_target"
    ]


def test_audit_rejects_nonregular_index_blob_even_if_worktree_is_regular(
    tmp_path: Path,
) -> None:
    """工作树替换不能掩盖 clean checkout 中的非普通 Git blob。

    Args:
        tmp_path: Pytest 提供的隔离仓库根目录。
    """

    target_path = Path("scripts/check.py")
    (tmp_path / target_path).parent.mkdir(parents=True)
    (tmp_path / target_path).write_text("# worktree regular\n", encoding="utf-8")
    target = AuthoritativeCommandTarget(
        Path("AGENTS.md"), target_path, "python scripts/check.py"
    )

    findings = audit_authoritative_command_targets(
        tmp_path,
        (target,),
        tracked_files={target_path},
        nonregular_tracked_files={target_path},
        require_tracked=True,
    )

    assert [finding.code for finding in findings] == [
        "nonregular_authoritative_command_target"
    ]
