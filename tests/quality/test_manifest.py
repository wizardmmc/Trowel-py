"""验证 moon manifest 只保存一份任务、依赖和 profile 事实。"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read_yaml(path: Path) -> dict[str, object]:
    """读取测试目标 YAML。"""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_manifest_promotes_cleared_baseline_checks_to_gate() -> None:
    """清零后的基础检查必须由默认 Gate 在本地与 CI 共同执行。"""
    root = _read_yaml(REPO_ROOT / "moon.yml")
    web = _read_yaml(REPO_ROOT / "web" / "moon.yml")
    root_tasks = root["tasks"]
    web_tasks = web["tasks"]

    assert {
        "backend.pytest",
        "backend.contracts",
        "backend.ruff",
        "backend.docstrings",
        "backend.mypy",
        "docs.context",
        "quality.selftest",
        "gate",
        "gate-full",
    } <= set(root_tasks)
    assert {
        "frontend.eslint",
        "frontend.typecheck",
        "frontend.agent-boundaries",
        "frontend.module-comments",
        "frontend.ui-contracts",
        "frontend.vitest",
        "frontend.build",
        "desktop.renderer",
        "desktop.settings",
        "desktop.diagnostic",
        "desktop.single-instance",
        "desktop.sidecar-hang",
        "desktop.renderer-crash",
        "desktop.shared-service",
        "desktop.agent-transport",
        "packaged.app",
        "packaged.settings",
        "packaged.default-paths",
        "packaged.residency",
        "packaged.renderer-crash",
        "packaged.agent-transport",
        "packaged.dmg-install",
    } <= set(web_tasks)

    gate_dependencies = set(root_tasks["gate"]["deps"])
    assert "root:backend.docstrings" in gate_dependencies
    assert "root:backend.mypy" in gate_dependencies
    assert "web:frontend.eslint" in gate_dependencies
    assert "root:desktop.agent-transport" not in gate_dependencies
    assert "root:docs.context" in gate_dependencies
    assert "root:quality.selftest" in gate_dependencies
    assert "root:backend.pytest" in gate_dependencies
    assert "web:frontend.vitest" in gate_dependencies


def test_mypy_strictness_and_eslint_warnings_are_frozen() -> None:
    """工具升级不能静默改变 strict 集合，ESLint warning 也不能通过。"""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    mypy = pyproject["tool"]["mypy"]
    strict_options = {
        "disallow_any_generics",
        "disallow_subclassing_any",
        "disallow_untyped_calls",
        "disallow_untyped_defs",
        "disallow_incomplete_defs",
        "check_untyped_defs",
        "disallow_untyped_decorators",
        "warn_redundant_casts",
        "warn_unused_ignores",
        "warn_return_any",
        "no_implicit_reexport",
        "strict_equality",
        "extra_checks",
    }
    assert {option for option in strict_options if mypy.get(option) is True} == strict_options
    assert mypy.get("strict") is not True

    package = json.loads((REPO_ROOT / "web" / "package.json").read_text(encoding="utf-8"))
    assert package["scripts"]["lint"] == "eslint . --max-warnings=0"


def test_contracts_depend_on_the_authoritative_frontend_build_leaf() -> None:
    """公开契约必须在任务图中依赖同一次前端构建。"""
    root = _read_yaml(REPO_ROOT / "moon.yml")
    contracts = root["tasks"]["backend.contracts"]

    assert contracts["deps"] == ["web:frontend.build"]


def test_electron_behavior_prepares_the_pinned_runtime_binary() -> None:
    """行为 E2E 启动前必须准备锁文件对应的 Electron 可执行文件。"""
    web = _read_yaml(REPO_ROOT / "web" / "moon.yml")
    e2e = _read_yaml(REPO_ROOT / "e2e" / "moon.yml")

    assert web["tasks"]["desktop.electron-prepare"]["command"] == [
        "bun",
        "run",
        "electron:prepare",
    ]
    assert web["tasks"]["desktop.electron-prepare"]["options"]["os"] == "macos"
    assert "web:desktop.electron-prepare" in e2e["tasks"]["behavior.prepare"]["deps"]
    behavior_tasks = {
        name: task
        for name, task in e2e["tasks"].items()
        if name == "behavior" or name.startswith("behavior.")
    }
    assert behavior_tasks
    assert all(task["options"]["os"] == "macos" for task in behavior_tasks.values())


def test_quality_tasks_disable_moon_cache_by_default() -> None:
    """质量结果不得复用未完整声明环境和产物的历史成功。"""
    root = _read_yaml(REPO_ROOT / "moon.yml")
    web = _read_yaml(REPO_ROOT / "web" / "moon.yml")

    assert root["taskOptions"]["cache"] is False
    assert web["taskOptions"]["cache"] is False
    assert all(
        task.get("options", {}).get("cache") is not True
        for task in root["tasks"].values()
    )
    assert all(
        task.get("options", {}).get("cache") is not True
        for task in web["tasks"].values()
    )


def test_ci_calls_one_authoritative_linux_gate() -> None:
    """Linux CI 获取任务差异所需历史，并只调用一个质量入口。"""
    workflow_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    workflow = workflow_path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(workflow)
    checkout = next(
        step
        for step in parsed["jobs"]["quality"]["steps"]
        if step.get("name") == "Check out repository"
    )

    assert "jobs:\n  quality:" in workflow
    assert checkout["with"]["fetch-depth"] == 0
    assert ".venv/bin/python -m scripts.quality gate --ci" in workflow
    assert "python -m pytest --ignore=tests/contracts" not in workflow
    assert "bun run typecheck" not in workflow
    assert "python -m pytest tests/contracts" not in workflow


def test_macos_behavior_ci_uploads_sanitized_leaf_diagnostics() -> None:
    """行为 Gate 失败时必须保留去敏产物和稳定叶子诊断。"""
    workflow = _read_yaml(REPO_ROOT / ".github" / "workflows" / "macos-behavior.yml")
    upload = next(
        step
        for step in workflow["jobs"]["behavior"]["steps"]
        if step.get("name") == "Upload sanitized behavior evidence"
    )
    paths = upload["with"]["path"].splitlines()

    assert paths == [".quality-runs/e2e/artifacts/"]
