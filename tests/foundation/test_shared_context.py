"""验证公共 Agent 上下文的 Git、链接和隐私边界。"""

import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.shared_context_check import (
    AUDIT_CONTROL_FILES,
    MODULE_CONTEXT_REQUIRED_HEADINGS,
    MODULE_CONTEXT_REQUIRED_STATUS_GROUPS,
    MODULE_CONTEXT_ROOTS,
    ROOT_CONTEXT_FILES,
    SHARED_CONTEXT_FILES,
    audit_repository,
    claude_imports,
    markdown_links,
    privacy_matches,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_AUDIT_CONTROL_FILES = (
    Path(".gitignore"),
    Path(".worktreeinclude"),
    Path("scripts/shared_context_check.py"),
    Path("tests/foundation/test_shared_context.py"),
)
EXPECTED_DISCUSSION_HEADINGS = (
    "领域职责与 owner",
    "不可破坏的不变量",
    "Agent runtime、Memory、Profile 与 telemetry 边界",
    "运行事实状态",
    "权威测试入口",
    "继续阅读",
)


def _create_minimal_repository(repo_root: Path) -> None:
    """建立只含公共上下文契约的隔离 Git 仓库。

    Args:
        repo_root: 用于创建并填充隔离仓库的测试目录。
    """

    subprocess.run(("git", "init", "--quiet", str(repo_root)), check=True)
    shutil.copy2(REPO_ROOT / ".gitignore", repo_root / ".gitignore")
    shutil.copy2(REPO_ROOT / ".worktreeinclude", repo_root / ".worktreeinclude")
    for relative_path in SHARED_CONTEXT_FILES:
        target = repo_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "@AGENTS.md\n" if relative_path.name == "CLAUDE.md" else "# Public\n",
            encoding="utf-8",
        )
    for module_root, headings in MODULE_CONTEXT_REQUIRED_HEADINGS.items():
        sections: list[str] = []
        for heading in headings:
            body = "Public."
            if heading == "运行事实状态":
                body = (
                    "| 状态 | 当前结论与证据边界 |\n"
                    "|---|---|\n"
                    "| `compatibility_required` | record=present; contract=public; "
                    "commit=abc1234; tests=tests/public.py |\n"
                    "| `unknown` | record=present; checked=public; gap=public; "
                    "do_not_claim=public; exit=public |"
                )
            sections.append(f"## {heading}\n\n{body}")
        (repo_root / module_root / "AGENTS.md").write_text(
            f"# Public\n\n{'\n\n'.join(sections)}\n",
            encoding="utf-8",
        )
    for relative_path in AUDIT_CONTROL_FILES:
        control_file = repo_root / relative_path
        if control_file.exists():
            continue
        control_file.parent.mkdir(parents=True, exist_ok=True)
        control_file.write_text("# Test audit control\n", encoding="utf-8")
    entrypoint_links = "\n".join(
        (
            "1. [README](README.md)",
            "2. [directory](directory.md)",
            "3. [PRD](docs/foundation/prd.md)",
            "4. [development](docs/foundation/development.md)",
        )
    )
    module_rows = "\n".join(
        f"| {module_root.name} scope | "
        f"[{module_root.name}]({module_root.as_posix()}/AGENTS.md) |"
        for module_root in MODULE_CONTEXT_ROOTS
    )
    (repo_root / "AGENTS.md").write_text(
        f"# Public\n\n## 开工入口\n\n{entrypoint_links}"
        "\n\n## 按领域继续读\n\n"
        "| 任务范围 | 公共入口 |\n|---|---|\n"
        f"{module_rows}\n",
        encoding="utf-8",
    )
    subprocess.run(
        (
            "git",
            "add",
            ".gitignore",
            ".worktreeinclude",
            *(str(path) for path in AUDIT_CONTROL_FILES),
            *(str(path) for path in SHARED_CONTEXT_FILES),
        ),
        cwd=repo_root,
        check=True,
    )


def test_repository_shared_context_is_publishable() -> None:
    """未提交开发树也必须完成文件存在、取消忽略、链接与隐私检查。"""

    findings = audit_repository(REPO_ROOT, require_tracked=False)

    assert findings == ()


def test_required_root_context_files_are_explicit() -> None:
    """fresh agent 的四个固定开工入口不能依赖递归链接偶然进入审计。"""

    expected = (
        Path("README.md"),
        Path("directory.md"),
        Path("docs/foundation/prd.md"),
        Path("docs/foundation/development.md"),
    )
    for relative_path in expected:
        assert relative_path in ROOT_CONTEXT_FILES


def test_audit_control_files_are_explicit() -> None:
    """门禁实现、测试和两份 Git 策略不能从自保护清单静默消失。"""

    assert AUDIT_CONTROL_FILES == EXPECTED_AUDIT_CONTROL_FILES


def test_repository_audit_rejects_missing_required_readme(tmp_path: Path) -> None:
    """固定根入口 README 缺失时不能靠删除索引链接绕过门禁。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    (tmp_path / "README.md").unlink()

    findings = audit_repository(tmp_path)

    assert (
        "missing_shared_file",
        Path("README.md"),
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_rejects_missing_root_entrypoint_links(
    tmp_path: Path,
) -> None:
    """根 AGENTS 必须显式保留四个按序开工入口。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    (tmp_path / "AGENTS.md").write_text(
        "# Public\n\n[discussion](trowel_py/discussion/AGENTS.md)\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert [
        finding.detail
        for finding in findings
        if finding.code == "missing_root_entrypoint"
    ] == [
        "root startup index must link to README.md",
        "root startup index must link to directory.md",
        "root startup index must link to docs/foundation/prd.md",
        "root startup index must link to docs/foundation/development.md",
    ]


def test_repository_audit_rejects_reordered_root_entrypoint_links(
    tmp_path: Path,
) -> None:
    """四个根开工入口都存在时仍必须保持约定的阅读顺序。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    (tmp_path / "AGENTS.md").write_text(
        "# Public\n\n"
        "## 开工入口\n\n"
        "1. [README](README.md)\n"
        "2. [directory](directory.md)\n"
        "3. [development](docs/foundation/development.md)\n"
        "4. [PRD](docs/foundation/prd.md)\n\n"
        "## 按领域继续读\n\n"
        "[development](docs/foundation/development.md)\n"
        "[discussion](trowel_py/discussion/AGENTS.md)\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        "misordered_root_entrypoints",
        Path("AGENTS.md"),
    ) in [(finding.code, finding.path) for finding in findings]


def test_discussion_module_context_files_are_registered() -> None:
    """首个领域入口和 Claude 桥接必须进入公共上下文清单。"""

    discussion_root = Path("trowel_py/discussion")
    assert discussion_root in MODULE_CONTEXT_ROOTS
    for module_root in MODULE_CONTEXT_ROOTS:
        assert module_root / "AGENTS.md" in SHARED_CONTEXT_FILES
        assert module_root / "CLAUDE.md" in SHARED_CONTEXT_FILES


def test_discussion_module_contract_is_explicit() -> None:
    """Discussion 首版模块模板不能随生产常量一起静默缩减。"""

    discussion_root = Path("trowel_py/discussion")
    assert MODULE_CONTEXT_REQUIRED_HEADINGS == {
        discussion_root: EXPECTED_DISCUSSION_HEADINGS
    }
    assert MODULE_CONTEXT_REQUIRED_STATUS_GROUPS == {
        discussion_root: (
            ("verified_live", "compatibility_required"),
            ("unknown",),
        )
    }


def test_repository_audit_rejects_empty_registered_module_context(
    tmp_path: Path,
) -> None:
    """已登记模块不能删除职责、边界、证据状态和权威测试后仍通过。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    module_agents = Path("trowel_py/discussion/AGENTS.md")
    (tmp_path / module_agents).write_text("# Public\n", encoding="utf-8")

    findings = audit_repository(tmp_path)

    finding_codes = [
        finding.code for finding in findings if finding.path == module_agents
    ]
    assert finding_codes.count("missing_module_section") == len(
        EXPECTED_DISCUSSION_HEADINGS
    )
    assert "missing_module_evidence_status" in finding_codes
    assert "missing_module_unknown" in finding_codes


def test_repository_audit_rejects_fact_statuses_outside_status_table(
    tmp_path: Path,
) -> None:
    """游离关键字不能替代运行事实表中的状态和证据记录。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    module_agents = Path("trowel_py/discussion/AGENTS.md")
    module_file = tmp_path / module_agents
    markdown = module_file.read_text(encoding="utf-8")
    markdown = markdown.replace(
        "| 状态 | 当前结论与证据边界 |\n"
        "|---|---|\n"
        "| `compatibility_required` | record=present; contract=public; "
        "commit=abc1234; tests=tests/public.py |\n"
        "| `unknown` | record=present; checked=public; gap=public; "
        "do_not_claim=public; exit=public |",
        "No structured facts yet.",
    )
    module_file.write_text(
        f"{markdown}\ncompatibility_required unknown\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    finding_codes = [
        finding.code for finding in findings if finding.path == module_agents
    ]
    assert "invalid_module_status_table" in finding_codes
    assert "missing_module_evidence_status" in finding_codes
    assert "missing_module_unknown" in finding_codes


@pytest.mark.parametrize(
    "bad_row",
    (
        "| `unexpected` | record=present; checked=public |",
        "| `unknown` | record=present; checked=duplicate; gap=duplicate; "
        "do_not_claim=duplicate; exit=duplicate |",
        "| `removal_candidate` | |",
    ),
)
def test_repository_audit_rejects_invalid_fact_status_rows(
    tmp_path: Path, bad_row: str
) -> None:
    """运行事实表拒绝未知状态、重复状态和空证据。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
        bad_row: 追加到合法状态表后的无效数据行。
    """

    _create_minimal_repository(tmp_path)
    module_agents = Path("trowel_py/discussion/AGENTS.md")
    module_file = tmp_path / module_agents
    markdown = module_file.read_text(encoding="utf-8")
    module_file.write_text(
        markdown.replace(
            "| `unknown` | record=present; checked=public; gap=public; "
            "do_not_claim=public; exit=public |",
            "| `unknown` | record=present; checked=public; gap=public; "
            f"do_not_claim=public; exit=public |\n{bad_row}",
        ),
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        "invalid_module_status_table",
        module_agents,
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_does_not_count_absent_live_record_as_evidence(
    tmp_path: Path,
) -> None:
    """明确写着暂无记录的 verified_live 行不能满足正向证据门禁。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    module_agents = Path("trowel_py/discussion/AGENTS.md")
    module_file = tmp_path / module_agents
    markdown = module_file.read_text(encoding="utf-8")
    module_file.write_text(
        markdown.replace(
            "| `compatibility_required` | record=present; contract=public; "
            "commit=abc1234; tests=tests/public.py |",
            "| `verified_live` | record=absent; checked=public; gap=public; "
            "do_not_claim=public; exit=public |",
        ),
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        "missing_module_evidence_status",
        module_agents,
    ) in [(finding.code, finding.path) for finding in findings]


def test_markdown_links_uses_commonmark_links_and_images() -> None:
    """链接提取覆盖行内、引用和图片语法，不扫描代码中的伪链接。"""

    markdown = """
[inline](docs/one.md)
[reference][target]
![image](screenshots/example.png)
`[code](private.md)`

[target]: docs/two.md
"""

    assert markdown_links(markdown) == (
        "docs/one.md",
        "docs/two.md",
        "screenshots/example.png",
    )


def test_claude_imports_ignores_code_examples() -> None:
    """Claude 导入识别只处理正文 text token，不把代码示例当成真实指令。"""

    markdown = """
@docs/reference/live.md
`@docs/reference/inline-example.md`

```text
@docs/reference/block-example.md
```
"""

    assert claude_imports(markdown) == ("docs/reference/live.md",)


def test_privacy_matches_known_private_shapes() -> None:
    """基础隐私扫描识别用户目录、凭据和真实 UUID 形态。"""

    markdown = (
        "/Users/example/project\n"
        "sk-ant-abcdefghijklmnopqrstuv\n"
        "018f2f7e-7b37-7cc2-8ef0-0123456789ab\n"
    )

    assert [code for code, _match in privacy_matches(markdown)] == [
        "absolute_user_path",
        "credential_shape",
        "uuid",
    ]


def test_privacy_matches_home_directory_at_boundary() -> None:
    """用户主目录后接 EOF 或标点时仍必须识别本机用户名。"""

    markdown = "/Users/alice\n/home/bob。\nC:\\Users\\carol"

    assert [code for code, _match in privacy_matches(markdown)] == [
        "absolute_user_path",
        "absolute_user_path",
        "windows_user_path",
    ]


@pytest.mark.parametrize(
    ("markdown", "expected_code"),
    (
        ("&#47;Users&#47;alice&#47;secret", "absolute_user_path"),
        (r"sk\-ant\-abcdefghijklmnopqrstuv", "credential_shape"),
        ("sk-**ant**-abcdefghijklmnopqrstuv", "credential_shape"),
    ),
)
def test_repository_audit_scans_commonmark_visible_text(
    tmp_path: Path, markdown: str, expected_code: str
) -> None:
    """字符引用、转义和展示标记不能拆开读者实际看到的隐私形态。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
        markdown: 原文不命中正则、渲染后会还原敏感形态的 CommonMark。
        expected_code: 可见文本投影必须触发的问题代码。
    """

    _create_minimal_repository(tmp_path)
    root_agents = tmp_path / "AGENTS.md"
    root_agents.write_text(
        f"{root_agents.read_text(encoding='utf-8')}{markdown}\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        expected_code,
        Path("AGENTS.md"),
    ) in [(finding.code, finding.path) for finding in findings]


@pytest.mark.parametrize(
    ("markdown", "expected_code"),
    (
        (
            '[public](README.md "&#47;Users&#47;alice&#47;secret")',
            "absolute_user_path",
        ),
        (
            '![public](screenshots/example.png "sk&#45;ant&#45;abcdefghijklmnopqrstuv")',
            "credential_shape",
        ),
    ),
)
def test_repository_audit_scans_commonmark_title_attributes(
    tmp_path: Path, markdown: str, expected_code: str
) -> None:
    """链接和图片悬停标题中的编码隐私形态不能绕过扫描。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
        markdown: title 属性会被 CommonMark 解码的链接或图片。
        expected_code: 解码后的属性值必须触发的问题代码。
    """

    _create_minimal_repository(tmp_path)
    root_agents = tmp_path / "AGENTS.md"
    root_agents.write_text(
        f"{root_agents.read_text(encoding='utf-8')}{markdown}\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        expected_code,
        Path("AGENTS.md"),
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_rejects_link_to_private_slice(tmp_path: Path) -> None:
    """公共入口不能把开发者自己的 slice 带入共享链接图。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    private_slice = tmp_path / "docs/slices/activate/slice-local.md"
    private_slice.parent.mkdir(parents=True)
    private_slice.write_text("# Private\n", encoding="utf-8")
    root_agents = tmp_path / "AGENTS.md"
    root_agents.write_text(
        f"{root_agents.read_text(encoding='utf-8')}"
        "[private](docs/slices/activate/slice-local.md)\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert [(finding.code, finding.path) for finding in findings] == [
        ("private_link", Path("AGENTS.md"))
    ]


def test_repository_audit_rejects_file_scheme_private_link(
    tmp_path: Path,
) -> None:
    """file scheme 不能把私人 slice 伪装成外部链接。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    root_agents = tmp_path / "AGENTS.md"
    root_agents.write_text(
        f"{root_agents.read_text(encoding='utf-8')}"
        "[private](file:///tmp/slice-local.md)\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        "invalid_local_link",
        Path("AGENTS.md"),
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_rejects_raw_html_link(tmp_path: Path) -> None:
    """原始 HTML 不能绕过 CommonMark 链接和隐私检查。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    root_agents = tmp_path / "AGENTS.md"
    root_agents.write_text(
        f"{root_agents.read_text(encoding='utf-8')}"
        '<a href="docs/slices/activate/slice-local.md">private</a>\n',
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        "forbidden_raw_html",
        Path("AGENTS.md"),
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_rejects_html_processing_instruction(
    tmp_path: Path,
) -> None:
    """README 中的 processing instruction 不能冒充白名单 HTML。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    (tmp_path / "README.md").write_text(
        "<?probe &#47;Users&#47;alice&#47;secret?>\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        "forbidden_raw_html",
        Path("README.md"),
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_decodes_privacy_shapes_in_markdown_url(
    tmp_path: Path,
) -> None:
    """Markdown 外链中的百分号编码不能隐藏本机用户目录。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    root_agents = tmp_path / "AGENTS.md"
    root_agents.write_text(
        f"{root_agents.read_text(encoding='utf-8')}"
        "[external](https://example.test/%2FUsers%2Falice%2Fsecret)\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        "absolute_user_path",
        Path("AGENTS.md"),
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_decodes_privacy_shapes_in_allowed_readme_html(
    tmp_path: Path,
) -> None:
    """README 允许的 HTML 图片地址也必须解码后做隐私扫描。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    (tmp_path / "README.md").write_text(
        '<img src="https://example.test/%2FUsers%2Falice%2Fsecret.png" '
        'alt="example" width="80%" />\n',
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        "absolute_user_path",
        Path("README.md"),
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_scans_allowed_readme_html_alt_text(
    tmp_path: Path,
) -> None:
    """README 图片替代文字中的字符引用也必须按可见文本扫描。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    (tmp_path / "README.md").write_text(
        '<img src="https://example.test/example.png" '
        'alt="&#47;Users&#47;alice&#47;secret" width="80%" />\n',
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        "absolute_user_path",
        Path("README.md"),
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_scans_allowed_readme_html_attributes(
    tmp_path: Path,
) -> None:
    """README 允许标签的非 URL 属性也必须解码后执行隐私扫描。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    (tmp_path / "README.md").write_text(
        '<p align="&#47;Users&#47;alice&#47;secret">Public</p>\n',
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        "absolute_user_path",
        Path("README.md"),
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_deduplicates_raw_and_structured_privacy_match(
    tmp_path: Path,
) -> None:
    """同一未编码链接不能被正文扫描和结构化扫描重复报告。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    root_agents = tmp_path / "AGENTS.md"
    root_agents.write_text(
        f"{root_agents.read_text(encoding='utf-8')}"
        "[external](https://example.test//Users/alice/secret)\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    privacy_findings = [
        finding
        for finding in findings
        if finding.code == "absolute_user_path" and finding.path == Path("AGENTS.md")
    ]
    assert len(privacy_findings) == 1


def test_repository_audit_rejects_private_claude_import(tmp_path: Path) -> None:
    """公共 AGENTS 不能用 Claude 专属导入绕过私人路径检查。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    root_agents = tmp_path / "AGENTS.md"
    root_agents.write_text(
        f"{root_agents.read_text(encoding='utf-8')}@docs/slices/activate/private.md\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        "forbidden_claude_import",
        Path("AGENTS.md"),
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_rejects_import_from_reachable_reference(
    tmp_path: Path,
) -> None:
    """递归到公共 reference 后仍必须拒绝其中的 Claude 专属导入。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    reference = Path("docs/reference/extra.md")
    (tmp_path / reference).write_text(
        "@docs/slices/activate/private.md\n", encoding="utf-8"
    )
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(
        f"{gitignore.read_text(encoding='utf-8')}!docs/reference/extra.md\n",
        encoding="utf-8",
    )
    subprocess.run(("git", "add", str(reference)), cwd=tmp_path, check=True)
    module_agents = tmp_path / "trowel_py/discussion/AGENTS.md"
    module_agents.write_text(
        f"{module_agents.read_text(encoding='utf-8')}"
        "[extra](../../docs/reference/extra.md)\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        "forbidden_claude_import",
        reference,
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_rejects_link_to_other_ignored_file(
    tmp_path: Path,
) -> None:
    """公共链接目标即使不在已知私人目录中也不能被忽略。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    ignored_note = tmp_path / "local-notes.md"
    ignored_note.write_text("# Local only\n", encoding="utf-8")
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(
        f"{gitignore.read_text(encoding='utf-8')}local-notes.md\n", encoding="utf-8"
    )
    root_agents = tmp_path / "AGENTS.md"
    root_agents.write_text(
        f"{root_agents.read_text(encoding='utf-8')}[local notes](local-notes.md)\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path, require_tracked=False)

    assert [(finding.code, finding.path) for finding in findings] == [
        ("ignored_link_target", Path("AGENTS.md"))
    ]


@pytest.mark.parametrize("ignore_source", ("info", "global"))
def test_repository_audit_rejects_machine_local_ignore_policy(
    tmp_path: Path, ignore_source: str
) -> None:
    """本机 exclude 不能替代仓库已跟踪的私人文件忽略规则。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
        ignore_source: 使用仓库本地 exclude 或用户级 ignore 文件的测试分支。
    """

    _create_minimal_repository(tmp_path)
    (tmp_path / ".gitignore").write_text("", encoding="utf-8")
    subprocess.run(("git", "add", ".gitignore"), cwd=tmp_path, check=True)
    private_path = Path("docs/private.md")
    (tmp_path / private_path).write_text("# Private\n", encoding="utf-8")
    if ignore_source == "info":
        exclude_file = tmp_path / ".git/info/exclude"
        exclude_file.write_text(f"{private_path}\n", encoding="utf-8")
    else:
        exclude_file = tmp_path / "machine-global-ignore"
        exclude_file.write_text(f"{private_path}\n", encoding="utf-8")
        subprocess.run(
            ("git", "config", "core.excludesFile", str(exclude_file)),
            cwd=tmp_path,
            check=True,
        )

    findings = audit_repository(tmp_path)

    assert (
        "exposed_private_file",
        private_path,
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_rejects_nested_gitignore_snapshot_mismatch(
    tmp_path: Path,
) -> None:
    """已跟踪的嵌套 `.gitignore` 也必须按同一 index/worktree 快照审计。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    nested_gitignore = Path("web/.gitignore")
    nested_file = tmp_path / nested_gitignore
    nested_file.parent.mkdir(parents=True)
    safe_content = "# Shared policy\n"
    nested_file.write_text("private-context.md\n", encoding="utf-8")
    subprocess.run(("git", "add", str(nested_gitignore)), cwd=tmp_path, check=True)
    nested_file.write_text(safe_content, encoding="utf-8")

    findings = audit_repository(tmp_path)

    assert (
        "staged_worktree_mismatch",
        nested_gitignore,
    ) in [(finding.code, finding.path) for finding in findings]


@pytest.mark.parametrize(
    "relative_path",
    ("config.toml", "CLAUDE-archived.md", "Learn.md", "progress.txt"),
)
def test_repository_audit_requires_private_root_ignore_rules(
    tmp_path: Path, relative_path: str
) -> None:
    """固定私人根文件未跟踪时也必须持续命中仓库 ignore 规则。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
        relative_path: 从仓库 `.gitignore` 删除规则的私人根文件。
    """

    _create_minimal_repository(tmp_path)
    gitignore = tmp_path / ".gitignore"
    lines = [
        line
        for line in gitignore.read_text(encoding="utf-8").splitlines()
        if line.strip() != relative_path
    ]
    gitignore.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run(("git", "add", ".gitignore"), cwd=tmp_path, check=True)
    (tmp_path / relative_path).write_text("# Private\n", encoding="utf-8")

    findings = audit_repository(tmp_path)

    assert (
        "exposed_private_file",
        Path(relative_path),
    ) in [(finding.code, finding.path) for finding in findings]


@pytest.mark.parametrize(
    ("ignore_rule", "probe_path"),
    (
        ("docs/*", "docs/slices/.shared-context-private"),
        ("spikes/", "spikes/.shared-context-private"),
    ),
)
def test_repository_audit_requires_private_directory_ignore_rules(
    tmp_path: Path, ignore_rule: str, probe_path: str
) -> None:
    """私人目录即使为空也必须由仓库规则覆盖其哨兵路径。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
        ignore_rule: 从仓库 `.gitignore` 删除的私人目录规则。
        probe_path: 删除规则后必须暴露的私人目录哨兵路径。
    """

    _create_minimal_repository(tmp_path)
    gitignore = tmp_path / ".gitignore"
    lines = [
        line
        for line in gitignore.read_text(encoding="utf-8").splitlines()
        if line.strip() != ignore_rule
    ]
    gitignore.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run(("git", "add", ".gitignore"), cwd=tmp_path, check=True)

    findings = audit_repository(tmp_path)

    assert (
        "exposed_private_file",
        Path(probe_path),
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_rejects_unindexed_module_context(tmp_path: Path) -> None:
    """根知识索引必须能到达每个已登记模块 AGENTS。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    (tmp_path / "AGENTS.md").write_text(
        "# Public\n\n"
        "## 开工入口\n\n"
        "1. [README](README.md)\n"
        "2. [directory](directory.md)\n"
        "3. [PRD](docs/foundation/prd.md)\n"
        "4. [development](docs/foundation/development.md)\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert [(finding.code, finding.path) for finding in findings] == [
        ("unindexed_module_context", Path("AGENTS.md"))
    ]


@pytest.mark.parametrize("mutation", ("outside", "empty_scope", "duplicate"))
def test_repository_audit_requires_scoped_unique_module_index_row(
    tmp_path: Path, mutation: str
) -> None:
    """模块入口必须在领域表格中拥有唯一且非空的任务范围摘要。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
        mutation: 把模块链接移到表外、清空摘要或复制整行的反例类型。
    """

    _create_minimal_repository(tmp_path)
    root_agents = tmp_path / "AGENTS.md"
    markdown = root_agents.read_text(encoding="utf-8")
    module_row = "| discussion scope | [discussion](trowel_py/discussion/AGENTS.md) |"
    if mutation == "outside":
        markdown = markdown.replace(module_row, "")
        markdown += "\n## 其他\n\n[discussion](trowel_py/discussion/AGENTS.md)\n"
    elif mutation == "empty_scope":
        markdown = markdown.replace(
            module_row, module_row.replace("discussion scope", "")
        )
    else:
        markdown = markdown.replace(module_row, f"{module_row}\n{module_row}")
    root_agents.write_text(markdown, encoding="utf-8")

    findings = audit_repository(tmp_path)

    assert (
        "unindexed_module_context",
        Path("AGENTS.md"),
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_rejects_image_as_module_index(tmp_path: Path) -> None:
    """模块图片不是可导航知识入口，不能满足根索引门禁。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    (tmp_path / "AGENTS.md").write_text(
        "# Public\n\n![discussion](trowel_py/discussion/AGENTS.md)\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        "unindexed_module_context",
        Path("AGENTS.md"),
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_rejects_nonminimal_module_claude_bridge(
    tmp_path: Path,
) -> None:
    """模块 CLAUDE 只能导入同级 AGENTS，不能拥有第二份领域正文。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    module_bridge = tmp_path / "trowel_py/discussion/CLAUDE.md"
    module_bridge.write_text("@AGENTS.md\n\n重复的领域规则。\n", encoding="utf-8")

    findings = audit_repository(tmp_path)

    assert [(finding.code, finding.path) for finding in findings] == [
        ("nonminimal_claude_bridge", Path("trowel_py/discussion/CLAUDE.md"))
    ]


def test_repository_audit_rejects_nonminimal_root_claude_bridge(
    tmp_path: Path,
) -> None:
    """根 CLAUDE 也只能导入共同 AGENTS，不能重新拥有全仓规则。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    root_bridge = tmp_path / "CLAUDE.md"
    root_bridge.write_text("@AGENTS.md\n\n重复的全仓规则。\n", encoding="utf-8")

    findings = audit_repository(tmp_path)

    assert [(finding.code, finding.path) for finding in findings] == [
        ("nonminimal_claude_bridge", Path("CLAUDE.md"))
    ]


def test_repository_audit_rejects_unregistered_nested_context(
    tmp_path: Path,
) -> None:
    """新增模块或孤立 Claude 入口未登记时不能绕过公共审计。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    memory_root = tmp_path / "trowel_py/memory"
    memory_root.mkdir(parents=True)
    (memory_root / "AGENTS.md").write_text("# Memory\n", encoding="utf-8")
    (tmp_path / "trowel_py/profile/CLAUDE.md").parent.mkdir(parents=True)
    (tmp_path / "trowel_py/profile/CLAUDE.md").write_text(
        "@AGENTS.md\n", encoding="utf-8"
    )

    findings = audit_repository(tmp_path, require_tracked=False)

    assert [
        (finding.code, finding.path)
        for finding in findings
        if finding.code == "unregistered_module_context"
    ] == [
        ("unregistered_module_context", Path("trowel_py/memory/AGENTS.md")),
        ("unregistered_module_context", Path("trowel_py/profile/CLAUDE.md")),
    ]


def test_repository_audit_rejects_untracked_agents_override(
    tmp_path: Path,
) -> None:
    """未跟踪的 Codex override 也不能替代仓库公共 AGENTS。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    override = Path("AGENTS.override.md")
    (tmp_path / override).write_text("# Local override\n", encoding="utf-8")

    findings = audit_repository(tmp_path, require_tracked=False)

    assert (
        "forbidden_agents_override",
        override,
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_rejects_tracked_nested_agents_override(
    tmp_path: Path,
) -> None:
    """进入 Git 的模块 override 仍必须被单一事实源门禁拒绝。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    override = Path("trowel_py/discussion/AGENTS.override.md")
    (tmp_path / override).write_text("# Module override\n", encoding="utf-8")
    subprocess.run(("git", "add", str(override)), cwd=tmp_path, check=True)

    findings = audit_repository(tmp_path)

    assert (
        "forbidden_agents_override",
        override,
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_rejects_ignored_agents_override(
    tmp_path: Path,
) -> None:
    """Git ignore 不能让 Codex override 逃过单一事实源门禁。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    override = Path("docs/private/AGENTS.override.md")
    (tmp_path / override).parent.mkdir(parents=True)
    (tmp_path / override).write_text("# Ignored override\n", encoding="utf-8")

    findings = audit_repository(tmp_path, require_tracked=False)

    assert (
        "forbidden_agents_override",
        override,
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_rejects_ignored_nested_context(
    tmp_path: Path,
) -> None:
    """ignored 文档树中的普通 AGENTS 与 CLAUDE 仍属于 runtime 上下文。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    private_root = tmp_path / "docs/private"
    private_root.mkdir(parents=True)
    (private_root / "AGENTS.md").write_text("# Ignored agents\n", encoding="utf-8")
    (private_root / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")

    findings = audit_repository(tmp_path, require_tracked=False)

    assert [
        (finding.code, finding.path)
        for finding in findings
        if finding.code == "unregistered_module_context"
    ] == [
        ("unregistered_module_context", Path("docs/private/AGENTS.md")),
        ("unregistered_module_context", Path("docs/private/CLAUDE.md")),
    ]


def test_repository_audit_rejects_claude_only_context(
    tmp_path: Path,
) -> None:
    """ignored 的 Claude 项目说明和 rules 不能形成第二份仓库事实源。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    claude_root = tmp_path / ".claude"
    (claude_root / "rules").mkdir(parents=True)
    (claude_root / "CLAUDE.md").write_text("# Claude project\n", encoding="utf-8")
    (claude_root / "rules/private.md").write_text("# Claude rule\n", encoding="utf-8")

    findings = audit_repository(tmp_path, require_tracked=False)

    assert [
        (finding.code, finding.path)
        for finding in findings
        if finding.code == "forbidden_claude_only_context"
    ] == [
        ("forbidden_claude_only_context", Path(".claude/CLAUDE.md")),
        ("forbidden_claude_only_context", Path(".claude/rules/private.md")),
    ]


@pytest.mark.parametrize("tracked", (False, True))
def test_repository_audit_rejects_codex_project_config(
    tmp_path: Path, tracked: bool
) -> None:
    """Codex 项目配置不能登记另一套任意文件名的说明来源。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
        tracked: 是否把 Codex 配置和 fallback 说明加入 Git 索引。
    """

    _create_minimal_repository(tmp_path)
    codex_config = Path(".codex/config.toml")
    fallback = Path("nested/TEAM_GUIDE.md")
    (tmp_path / codex_config).parent.mkdir(parents=True)
    (tmp_path / codex_config).write_text(
        'project_doc_fallback_filenames = ["TEAM_GUIDE.md"]\n',
        encoding="utf-8",
    )
    (tmp_path / fallback).parent.mkdir(parents=True)
    (tmp_path / fallback).write_text("# Codex only\n", encoding="utf-8")
    if tracked:
        subprocess.run(
            ("git", "add", "-f", str(codex_config), str(fallback)),
            cwd=tmp_path,
            check=True,
        )

    findings = audit_repository(tmp_path, require_tracked=tracked)

    assert (
        "forbidden_codex_only_context",
        codex_config,
    ) in [(finding.code, finding.path) for finding in findings]


@pytest.mark.parametrize(
    ("relative_path", "expected_code"),
    (
        ("trowel_py/memory/agents.md", "unregistered_module_context"),
        ("trowel_py/profile/claude.md", "unregistered_module_context"),
        (".CLAUDE/rules/private.md", "forbidden_claude_only_context"),
    ),
)
def test_repository_audit_rejects_noncanonical_context_case(
    tmp_path: Path,
    relative_path: str,
    expected_code: str,
) -> None:
    """macOS 可加载的非规范大小写说明也不能绕过公共事实源门禁。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
        relative_path: 使用非规范大小写的 runtime 说明路径。
        expected_code: 该类路径必须触发的问题代码。
    """

    _create_minimal_repository(tmp_path)
    context_path = Path(relative_path)
    (tmp_path / context_path).parent.mkdir(parents=True)
    (tmp_path / context_path).write_text("# Noncanonical\n", encoding="utf-8")

    findings = audit_repository(tmp_path, require_tracked=False)

    assert (
        expected_code,
        context_path,
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_allows_ignored_root_claude_local(tmp_path: Path) -> None:
    """根目录 ignored 的 CLAUDE.local 仍可承载当前工作树临时补充。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    (tmp_path / "CLAUDE.local.md").write_text("# Local only\n", encoding="utf-8")

    findings = audit_repository(tmp_path, require_tracked=False)

    assert findings == ()


@pytest.mark.parametrize("tracked", (False, True))
def test_repository_audit_rejects_nested_claude_local(
    tmp_path: Path, tracked: bool
) -> None:
    """模块级 CLAUDE.local 无论 ignored 还是强制跟踪都属于第二事实源。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
        tracked: 是否强制把模块本地说明加入 Git 索引。
    """

    _create_minimal_repository(tmp_path)
    local_context = Path("trowel_py/discussion/CLAUDE.local.md")
    (tmp_path / local_context).write_text("# Nested local\n", encoding="utf-8")
    if tracked:
        subprocess.run(
            ("git", "add", "-f", str(local_context)), cwd=tmp_path, check=True
        )

    findings = audit_repository(tmp_path, require_tracked=tracked)

    assert (
        "forbidden_nested_claude_local",
        local_context,
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_ignores_untracked_claude_worktree_context(
    tmp_path: Path,
) -> None:
    """Claude 自管嵌套 worktree 的未跟踪副本不属于父仓库事实源。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    worktree_context = tmp_path / ".claude/worktrees/probe/AGENTS.md"
    worktree_context.parent.mkdir(parents=True)
    worktree_context.write_text("# Nested worktree\n", encoding="utf-8")

    findings = audit_repository(tmp_path, require_tracked=False)

    assert findings == ()


@pytest.mark.parametrize(
    ("relative_path", "expected_code"),
    (
        ("output/AGENTS.override.md", "forbidden_agents_override"),
        ("tmp/CLAUDE.md", "unregistered_module_context"),
    ),
)
def test_repository_audit_checks_untracked_runtime_context_in_local_data(
    tmp_path: Path,
    relative_path: str,
    expected_code: str,
) -> None:
    """仓库自有运行目录不能因 Git ignore 而形成未审计的第二指令源。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
        relative_path: 放入 ignored 运行目录的说明文件路径。
        expected_code: 该类说明必须触发的问题代码。
    """

    _create_minimal_repository(tmp_path)
    context_path = Path(relative_path)
    (tmp_path / context_path).parent.mkdir(parents=True)
    (tmp_path / context_path).write_text("# Local context\n", encoding="utf-8")

    findings = audit_repository(tmp_path, require_tracked=False)

    assert (
        expected_code,
        context_path,
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_follows_public_reference_links(tmp_path: Path) -> None:
    """共享入口新链接的公共 reference 正文也必须接受隐私和链接审计。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    reference = Path("docs/reference/extra.md")
    (tmp_path / reference).write_text("/Users/example/project\n", encoding="utf-8")
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(
        f"{gitignore.read_text(encoding='utf-8')}!docs/reference/extra.md\n",
        encoding="utf-8",
    )
    subprocess.run(("git", "add", str(reference)), cwd=tmp_path, check=True)
    module_agents = tmp_path / "trowel_py/discussion/AGENTS.md"
    module_agents.write_text(
        f"{module_agents.read_text(encoding='utf-8')}"
        "[extra](../../docs/reference/extra.md)\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        "absolute_user_path",
        reference,
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_follows_root_markdown_links(tmp_path: Path) -> None:
    """根目录公共 Markdown 的正文和二级链接也属于可达审计闭包。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    public_doc = Path("CONTRIBUTING.md")
    (tmp_path / public_doc).write_text(
        "[private](docs/slices/activate/private.md)\n", encoding="utf-8"
    )
    subprocess.run(("git", "add", str(public_doc)), cwd=tmp_path, check=True)
    root_agents = tmp_path / "AGENTS.md"
    root_agents.write_text(
        f"{root_agents.read_text(encoding='utf-8')}[contributing](CONTRIBUTING.md)\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        "private_link",
        public_doc,
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_follows_markdown_extension(tmp_path: Path) -> None:
    """`.markdown` 导航目标和 `.md` 一样必须递归审计正文。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    public_doc = Path("docs/reference/extra.markdown")
    (tmp_path / public_doc).write_text(
        "[private](../slices/activate/private.md)\n", encoding="utf-8"
    )
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(
        f"{gitignore.read_text(encoding='utf-8')}!docs/reference/extra.markdown\n",
        encoding="utf-8",
    )
    subprocess.run(("git", "add", str(public_doc)), cwd=tmp_path, check=True)
    root_agents = tmp_path / "AGENTS.md"
    root_agents.write_text(
        f"{root_agents.read_text(encoding='utf-8')}"
        "[extra](docs/reference/extra.markdown)\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        "private_link",
        public_doc,
    ) in [(finding.code, finding.path) for finding in findings]


@pytest.mark.parametrize("suffix", (".txt", ".html", ".json"))
def test_repository_audit_rejects_non_markdown_context_document(
    tmp_path: Path, suffix: str
) -> None:
    """项目事实不能通过更换文本扩展名退出递归隐私审计。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
        suffix: 不受公共上下文解析器支持的文本扩展名。
    """

    _create_minimal_repository(tmp_path)
    public_doc = Path(f"context{suffix}")
    (tmp_path / public_doc).write_text("/Users/alice\n", encoding="utf-8")
    subprocess.run(("git", "add", str(public_doc)), cwd=tmp_path, check=True)
    root_agents = tmp_path / "AGENTS.md"
    root_agents.write_text(
        f"{root_agents.read_text(encoding='utf-8')}[context]({public_doc})\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        "unsupported_navigation_target",
        Path("AGENTS.md"),
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_rejects_markdown_hidden_as_image(tmp_path: Path) -> None:
    """Markdown 文档不能用图片语法进入公共白名单却跳过递归审计。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    root_agents = tmp_path / "AGENTS.md"
    root_agents.write_text(
        f"{root_agents.read_text(encoding='utf-8')}"
        "![hidden](docs/reference/agent-runtime.md)\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        "markdown_image_target",
        Path("AGENTS.md"),
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_checks_html_links_in_reachable_markdown(
    tmp_path: Path,
) -> None:
    """允许普通公共文档使用 HTML，但其中链接仍不能逃过结构化审计。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    public_doc = Path("README.md")
    (tmp_path / public_doc).write_text(
        '<a href="docs/slices/activate/private.md">private</a>\n', encoding="utf-8"
    )
    subprocess.run(("git", "add", str(public_doc)), cwd=tmp_path, check=True)
    root_agents = tmp_path / "AGENTS.md"
    root_agents.write_text(
        f"{root_agents.read_text(encoding='utf-8')}[readme](README.md)\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        "private_link",
        public_doc,
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_rejects_unapproved_html_url_attribute(
    tmp_path: Path,
) -> None:
    """README 白名单外的 srcset 等 URL 属性不能形成未审计引用。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    public_doc = Path("README.md")
    (tmp_path / public_doc).write_text(
        '<img srcset="docs/slices/private.png 2x" alt="private" />\n',
        encoding="utf-8",
    )
    subprocess.run(("git", "add", str(public_doc)), cwd=tmp_path, check=True)
    root_agents = tmp_path / "AGENTS.md"
    root_agents.write_text(
        f"{root_agents.read_text(encoding='utf-8')}[readme](README.md)\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        "forbidden_raw_html",
        public_doc,
    ) in [(finding.code, finding.path) for finding in findings]


@pytest.mark.parametrize(
    "relative_path",
    (
        "docs/slices/activate/private.md",
        "docs/milestones/private.md",
        "docs/reference/private-notes.md",
        "docs/foundation/private-notes.md",
        "CLAUDE.local.md",
        "CLAUDE-archived.md",
        "Learn.md",
        "config.toml",
        "progress.txt",
        "spikes/probe.json",
    ),
)
def test_repository_audit_rejects_tracked_private_files(
    tmp_path: Path, relative_path: str
) -> None:
    """私人规划和本地 Claude 补充即使被强制 add 也不能进入公共提交。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
        relative_path: 被强制加入 Git 索引的私人文件。
    """

    _create_minimal_repository(tmp_path)
    private_path = Path(relative_path)
    (tmp_path / private_path).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / private_path).write_text("# Private\n", encoding="utf-8")
    subprocess.run(("git", "add", "-f", str(private_path)), cwd=tmp_path, check=True)

    findings = audit_repository(tmp_path)

    assert (
        "tracked_private_file",
        private_path,
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_rejects_external_shared_file_symlink(
    tmp_path: Path,
) -> None:
    """已登记公共上下文不能通过符号链接读取仓库外正文。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    external = tmp_path.parent / f"{tmp_path.name}-external.md"
    external.write_text("# External\n", encoding="utf-8")
    module_agents = tmp_path / "trowel_py/discussion/AGENTS.md"
    module_agents.unlink()
    module_agents.symlink_to(external)

    findings = audit_repository(tmp_path)

    assert (
        "symlink_shared_file",
        Path("trowel_py/discussion/AGENTS.md"),
    ) in [(finding.code, finding.path) for finding in findings]


@pytest.mark.parametrize("tracked", (False, True))
def test_repository_audit_rejects_context_directory_symlink(
    tmp_path: Path, tracked: bool
) -> None:
    """目录链接无论是否跟踪都不能藏入另一组运行时说明。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
        tracked: 是否把目录链接加入 Git 索引。
    """

    _create_minimal_repository(tmp_path)
    external = tmp_path.parent / f"{tmp_path.name}-context"
    external.mkdir()
    (external / "AGENTS.md").write_text("# Hidden context\n", encoding="utf-8")
    alias = Path("linked-context")
    (tmp_path / alias).symlink_to(external, target_is_directory=True)
    if tracked:
        subprocess.run(("git", "add", str(alias)), cwd=tmp_path, check=True)

    findings = audit_repository(tmp_path, require_tracked=tracked)

    assert (
        "symlink_context_directory",
        alias,
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_rejects_tracked_media_symlink_to_private_file(
    tmp_path: Path,
) -> None:
    """已跟踪媒体别名不能通过符号链接引用 ignored 私人文件。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    private_media = tmp_path / "docs/slices/private.png"
    private_media.parent.mkdir(parents=True)
    private_media.write_bytes(b"private")
    alias = Path("screenshots/alias.png")
    (tmp_path / alias).parent.mkdir(parents=True)
    (tmp_path / alias).symlink_to("../docs/slices/private.png")
    subprocess.run(("git", "add", str(alias)), cwd=tmp_path, check=True)
    root_agents = tmp_path / "AGENTS.md"
    root_agents.write_text(
        f"{root_agents.read_text(encoding='utf-8')}![alias]({alias})\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        "symlink_link_target",
        Path("AGENTS.md"),
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_rejects_gitlink_as_public_media(tmp_path: Path) -> None:
    """Git submodule 入口不能凭后缀冒充 clean checkout 可读的媒体文件。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    alias = Path("screenshots/asset.png")
    subprocess.run(
        (
            "git",
            "update-index",
            "--add",
            "--info-only",
            "--cacheinfo",
            f"160000,{'1' * 40},{alias}",
        ),
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / alias).mkdir(parents=True)
    root_agents = tmp_path / "AGENTS.md"
    root_agents.write_text(
        f"{root_agents.read_text(encoding='utf-8')}![asset]({alias})\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        "nonregular_link_target",
        Path("AGENTS.md"),
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_checks_untracked_regular_alias(tmp_path: Path) -> None:
    """普通本地链接目标必须按别名本身检查 Git 跟踪状态。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    alias = tmp_path / "alias.md"
    alias.write_text("# Public alias\n", encoding="utf-8")
    root_agents = tmp_path / "AGENTS.md"
    root_agents.write_text(
        f"{root_agents.read_text(encoding='utf-8')}[alias](alias.md)\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        "untracked_link_target",
        Path("AGENTS.md"),
    ) in [(finding.code, finding.path) for finding in findings]


@pytest.mark.parametrize(
    ("relative_path", "expected_code"),
    (
        ("spikes/embedded/AGENTS.override.md", "forbidden_agents_override"),
        ("spikes/embedded/AGENTS.md", "unregistered_module_context"),
        ("spikes/embedded/CLAUDE.md", "unregistered_module_context"),
        (
            "spikes/embedded/.claude/rules/private.md",
            "forbidden_claude_only_context",
        ),
        (
            ".claude/worktrees/probe/AGENTS.md",
            "forbidden_claude_only_context",
        ),
        (
            ".claude/worktrees/probe/.claude/rules/private.md",
            "forbidden_claude_only_context",
        ),
    ),
)
def test_repository_audit_checks_tracked_context_inside_excluded_tree(
    tmp_path: Path,
    relative_path: str,
    expected_code: str,
) -> None:
    """排除规则只能跳过私有工作树，不能跳过强制跟踪的 runtime 说明。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    context_path = Path(relative_path)
    (tmp_path / context_path).parent.mkdir(parents=True)
    (tmp_path / context_path).write_text("# Tracked context\n", encoding="utf-8")
    subprocess.run(("git", "add", "-f", str(context_path)), cwd=tmp_path, check=True)

    findings = audit_repository(tmp_path)

    assert (
        expected_code,
        context_path,
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_rejects_untracked_registered_module(
    tmp_path: Path,
) -> None:
    """已登记模块文件从 Git 索引移除后必须由 clean-checkout 门禁拦截。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    module_agents = Path("trowel_py/discussion/AGENTS.md")
    subprocess.run(
        ("git", "rm", "--cached", "--quiet", str(module_agents)),
        cwd=tmp_path,
        check=True,
    )

    findings = audit_repository(tmp_path)

    assert (
        "untracked_shared_file",
        module_agents,
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_rejects_staged_worktree_mismatch(tmp_path: Path) -> None:
    """严格门禁不能用安全工作树正文替代已暂存的危险正文。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    root_agents = tmp_path / "AGENTS.md"
    safe_markdown = root_agents.read_text(encoding="utf-8")
    root_agents.write_text(
        f"{safe_markdown}@docs/slices/activate/private.md\n", encoding="utf-8"
    )
    subprocess.run(("git", "add", "AGENTS.md"), cwd=tmp_path, check=True)
    root_agents.write_text(safe_markdown, encoding="utf-8")

    findings = audit_repository(tmp_path)

    assert (
        "staged_worktree_mismatch",
        Path("AGENTS.md"),
    ) in [(finding.code, finding.path) for finding in findings]


@pytest.mark.parametrize("index_flag", ("--assume-unchanged", "--skip-worktree"))
def test_repository_audit_rejects_hidden_index_worktree_mismatch(
    tmp_path: Path, index_flag: str
) -> None:
    """Git 特殊索引标志不能让安全工作树正文掩盖危险暂存正文。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
        index_flag: 会让普通 ``git diff`` 隐藏路径差异的索引标志。
    """

    _create_minimal_repository(tmp_path)
    root_agents = tmp_path / "AGENTS.md"
    safe_markdown = root_agents.read_text(encoding="utf-8")
    root_agents.write_text(
        f"{safe_markdown}@docs/slices/activate/private.md\n",
        encoding="utf-8",
    )
    subprocess.run(("git", "add", "AGENTS.md"), cwd=tmp_path, check=True)
    subprocess.run(
        ("git", "update-index", index_flag, "AGENTS.md"),
        cwd=tmp_path,
        check=True,
    )
    root_agents.write_text(safe_markdown, encoding="utf-8")

    findings = audit_repository(tmp_path)

    assert (
        "special_index_flag",
        Path("AGENTS.md"),
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_rejects_intent_to_add_link_target(tmp_path: Path) -> None:
    """`git add -N` 不能让未暂存媒体目标冒充已跟踪文件。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    asset = Path("screenshots/local.png")
    (tmp_path / asset).parent.mkdir(parents=True)
    (tmp_path / asset).write_bytes(b"not-a-real-image")
    subprocess.run(("git", "add", "-N", str(asset)), cwd=tmp_path, check=True)
    root_agents = tmp_path / "AGENTS.md"
    root_agents.write_text(
        f"{root_agents.read_text(encoding='utf-8')}![local]({asset})\n",
        encoding="utf-8",
    )

    findings = audit_repository(tmp_path)

    assert (
        "staged_worktree_mismatch",
        asset,
    ) in [(finding.code, finding.path) for finding in findings]


@pytest.mark.parametrize("relative_path", EXPECTED_AUDIT_CONTROL_FILES)
def test_repository_audit_rejects_untracked_audit_control(
    tmp_path: Path, relative_path: Path
) -> None:
    """门禁支撑文件从索引移除后不能靠工作树副本继续通过严格模式。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
        relative_path: 从 Git 索引移除但保留在工作树的门禁支撑文件。
    """

    _create_minimal_repository(tmp_path)
    subprocess.run(
        ("git", "rm", "--cached", "--quiet", str(relative_path)),
        cwd=tmp_path,
        check=True,
    )

    findings = audit_repository(tmp_path)

    assert (
        "untracked_audit_control",
        relative_path,
    ) in [(finding.code, finding.path) for finding in findings]


@pytest.mark.parametrize(
    "relative_path",
    (
        Path(".worktreeinclude"),
        Path("scripts/shared_context_check.py"),
        Path("tests/foundation/test_shared_context.py"),
    ),
)
def test_repository_audit_rejects_symlink_audit_control(
    tmp_path: Path, relative_path: Path
) -> None:
    """审计脚本、测试和工作树策略不能以已跟踪符号链接冒充控制文件。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
        relative_path: 需要替换成 mode 120000 的审计控制文件。
    """

    _create_minimal_repository(tmp_path)
    control_file = tmp_path / relative_path
    target = tmp_path / f"control-target-{relative_path.name}"
    target.write_bytes(control_file.read_bytes())
    control_file.unlink()
    control_file.symlink_to(target)
    subprocess.run(("git", "add", "-f", str(relative_path)), cwd=tmp_path, check=True)

    findings = audit_repository(tmp_path)

    assert (
        "nonregular_audit_control",
        relative_path,
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_rejects_staged_private_file_missing_from_worktree(
    tmp_path: Path,
) -> None:
    """私人文件暂存后即使从工作树删除，也必须按索引内容阻断。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    private_path = Path("docs/slices/activate/private.md")
    (tmp_path / private_path).parent.mkdir(parents=True)
    (tmp_path / private_path).write_text("# Private\n", encoding="utf-8")
    subprocess.run(("git", "add", "-f", str(private_path)), cwd=tmp_path, check=True)
    (tmp_path / private_path).unlink()

    findings = audit_repository(tmp_path)

    assert (
        "tracked_private_file",
        private_path,
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_rejects_missing_module_claude_bridge(
    tmp_path: Path,
) -> None:
    """已登记模块缺少 Claude 桥接文件时必须失败。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    module_bridge = Path("trowel_py/discussion/CLAUDE.md")
    (tmp_path / module_bridge).unlink()

    findings = audit_repository(tmp_path)

    assert (
        "missing_shared_file",
        module_bridge,
    ) in [(finding.code, finding.path) for finding in findings]


def test_repository_audit_rejects_module_bridge_without_import(
    tmp_path: Path,
) -> None:
    """模块 Claude 没有导入同级 AGENTS 时必须失败。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    module_bridge = Path("trowel_py/discussion/CLAUDE.md")
    (tmp_path / module_bridge).write_text("# Claude only\n", encoding="utf-8")

    findings = audit_repository(tmp_path)

    assert (
        "missing_claude_bridge",
        module_bridge,
    ) in [(finding.code, finding.path) for finding in findings]
