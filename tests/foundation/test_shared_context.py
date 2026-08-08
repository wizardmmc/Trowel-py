"""验证公共 Agent 上下文的 Git、链接和隐私边界。"""

import shutil
import subprocess
from pathlib import Path

from scripts.shared_context_check import (
    SHARED_CONTEXT_FILES,
    audit_repository,
    markdown_links,
    privacy_matches,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


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
            "@AGENTS.md\n" if relative_path == Path("CLAUDE.md") else "# Public\n",
            encoding="utf-8",
        )
    subprocess.run(
        (
            "git",
            "add",
            ".gitignore",
            ".worktreeinclude",
            *(str(path) for path in SHARED_CONTEXT_FILES),
        ),
        cwd=repo_root,
        check=True,
    )


def test_repository_shared_context_is_publishable() -> None:
    """未提交开发树也必须完成文件存在、取消忽略、链接与隐私检查。"""

    findings = audit_repository(REPO_ROOT, require_tracked=False)

    assert findings == ()


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


def test_repository_audit_rejects_link_to_private_slice(tmp_path: Path) -> None:
    """公共入口不能把开发者自己的 slice 带入共享链接图。

    Args:
        tmp_path: Pytest 为本反例提供的隔离临时目录。
    """

    _create_minimal_repository(tmp_path)
    private_slice = tmp_path / "docs/slices/activate/slice-local.md"
    private_slice.parent.mkdir(parents=True)
    private_slice.write_text("# Private\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(
        "[private](docs/slices/activate/slice-local.md)\n", encoding="utf-8"
    )

    findings = audit_repository(tmp_path)

    assert [(finding.code, finding.path) for finding in findings] == [
        ("private_link", Path("AGENTS.md"))
    ]


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
    (tmp_path / "AGENTS.md").write_text(
        "[local notes](local-notes.md)\n", encoding="utf-8"
    )

    findings = audit_repository(tmp_path, require_tracked=False)

    assert [(finding.code, finding.path) for finding in findings] == [
        ("ignored_link_target", Path("AGENTS.md"))
    ]
