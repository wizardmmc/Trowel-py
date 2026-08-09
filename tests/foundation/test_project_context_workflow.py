"""验证项目知识更新已经成为公开的 slice 收尾契约。"""

from pathlib import Path
import subprocess

import yaml
from markdown_it import MarkdownIt


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_development_defines_project_context_handoff() -> None:
    """开发流程必须说明稳定结论检查、Git diff 候选和审查责任。"""

    development = (REPO_ROOT / "docs/foundation/development.md").read_text(
        encoding="utf-8"
    )

    assert "## Slice 收尾与项目知识更新" in development
    assert "`none + 原因`" in development
    assert "目标文档 diff" in development
    assert "独立 AIRC" in development
    assert "不能阻止会话关闭" in development
    assert "daily review" in development
    yaml_records = [
        yaml.safe_load(token.content)
        for token in MarkdownIt("commonmark").parse(development)
        if token.type == "fence" and token.info.strip() == "yaml"
    ]
    candidates = [
        record
        for record in yaml_records
        if isinstance(record, dict) and "target" in record
    ]
    none_records = [record for record in yaml_records if record == {"none": "原因"}]
    assert len(yaml_records) == 2
    assert len(candidates) == 1
    assert len(none_records) == 1
    candidate = candidates[0]
    assert tuple(candidate) == (
        "target",
        "claim",
        "evidence",
        "scope",
        "reason",
        "review",
    )
    assert candidate["review"] == "接纳 | 修改 | 延后 | 拒绝"


def test_root_verification_lists_authoritative_quality_gate() -> None:
    """fresh agent 必须从根验证入口找到包含 freshness 的权威 Gate。"""

    root_agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    manifest = yaml.safe_load((REPO_ROOT / "moon.yml").read_text(encoding="utf-8"))

    assert ".venv/bin/python -m scripts.quality gate" in root_agents
    assert "root:docs.context" in manifest["tasks"]["gate"]["deps"]


def test_quality_gate_keeps_context_entrypoint_and_scripts_lint() -> None:
    """CI 经权威任务图执行严格 freshness，并继续 lint scripts 包。"""

    workflow = yaml.safe_load(
        (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    )
    quality_steps = {
        step["name"]: step
        for step in workflow["jobs"]["quality"]["steps"]
        if "name" in step
    }
    manifest = yaml.safe_load((REPO_ROOT / "moon.yml").read_text(encoding="utf-8"))
    tasks = manifest["tasks"]

    assert quality_steps["Run authoritative quality gate"]["run"] == (
        ".venv/bin/python -m scripts.quality gate --ci --output .quality-runs/ci"
    )
    assert tasks["docs.context"]["command"] == [
        ".venv/bin/python",
        "-m",
        "scripts.quality.context_task",
    ]
    assert tasks["backend.ruff"]["command"] == [
        ".venv/bin/python",
        "-m",
        "ruff",
        "check",
        "trowel_py",
        "scripts",
        "tests",
    ]


def test_context_freshness_module_entrypoint_is_runnable() -> None:
    """文档和 CI 使用的模块入口必须能加载组合出的检查组件。"""

    completed = subprocess.run(
        (
            str(REPO_ROOT / ".venv/bin/python"),
            "-m",
            "scripts.shared_context_check",
            "--allow-untracked",
        ),
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_context_freshness_file_entrypoint_remains_compatible() -> None:
    """已有本地自动化仍可通过历史脚本路径运行同一检查器。"""

    completed = subprocess.run(
        (
            str(REPO_ROOT / ".venv/bin/python"),
            "scripts/shared_context_check.py",
            "--allow-untracked",
        ),
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
