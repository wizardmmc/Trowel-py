"""验证 moon 原始事实到 四态摘要的纯转换。"""

from __future__ import annotations

from scripts.quality.models import QualityStatus
from scripts.quality.report import normalize_moon_run


def _action(
    target: str, status: str, operation_type: str, exit_code: int | None = None
) -> dict:
    """构造从 moon 2.4.6 真实报告裁剪出的 action shape。"""
    metadata: dict[str, object] = {"type": operation_type}
    if exit_code is not None:
        metadata["exitCode"] = exit_code
    return {
        "duration": {"secs": 0, "nanos": 20_000_000},
        "node": {"params": {"target": target}},
        "operations": [{"status": status, "meta": metadata}],
        "status": status,
    }


def _graph() -> dict:
    """构造 moon 2.4.6 action-graph 的真实边方向。"""
    return {
        "graph": {"edges": [[0, 1, "required"], [2, 3, "required"]]},
        "data": {
            "0": {"params": {"target": "root:backend.contracts"}},
            "1": {"params": {"target": "web:frontend.build"}},
            "2": {"params": {"target": "root:gate"}},
            "3": {"params": {"target": "root:backend.contracts"}},
        },
    }


def _definitions() -> dict[str, dict]:
    """构造 moon 2.4.6 `moon task --json` 返回的相关字段。"""
    return {
        "web:frontend.build": {
            "id": "frontend.build",
            "description": "生成公开契约使用的本次前端产物。",
            "options": {"cache": False},
        },
        "root:backend.contracts": {
            "id": "backend.contracts",
            "description": "检查公开契约。",
            "options": {"cache": False},
        },
        "root:docs.context": {
            "id": "docs.context",
            "description": "检查公共项目上下文。",
            "options": {"cache": False},
        },
        "root:desktop.renderer": {
            "id": "desktop.renderer",
            "description": "仅在 macOS 运行 renderer smoke。",
            "options": {"cache": False, "os": ["macos"]},
        },
        "root:gate": {
            "id": "gate",
            "description": "默认聚合入口。",
            "options": {"cache": False},
        },
    }


def test_normalize_run_distinguishes_failed_blocked_and_not_applicable() -> None:
    """失败依赖只阻断后继，平台 no-op 不得显示成通过。"""
    report = {
        "actions": [
            _action("web:frontend.build", "failed", "task-execution", 9),
            _action("root:backend.contracts", "skipped", "task-execution"),
            _action("root:docs.context", "passed", "task-execution", 0),
            _action("root:desktop.renderer", "passed", "no-operation"),
            _action("root:gate", "skipped", "task-execution"),
        ]
    }

    summary = normalize_moon_run(report, _graph(), _definitions(), current_os="linux")
    statuses = {result.id: result.status for result in summary.results}

    assert statuses == {
        "backend.contracts": QualityStatus.BLOCKED,
        "desktop.renderer": QualityStatus.NOT_APPLICABLE,
        "docs.context": QualityStatus.PASSED,
        "frontend.build": QualityStatus.FAILED,
    }
    assert summary.exit_code == 1


def test_normalize_run_rejects_cached_or_unexplained_noop_results() -> None:
    """缓存命中和无平台依据的 no-op 都是适配配置错误。"""
    report = {
        "actions": [
            _action("root:docs.context", "cached", "output-hydration", 0),
            _action("web:frontend.build", "passed", "no-operation"),
        ]
    }

    summary = normalize_moon_run(
        report, {"graph": {"edges": []}, "data": {}}, _definitions(), current_os="linux"
    )
    statuses = {result.id: result.status for result in summary.results}

    assert statuses == {
        "docs.context": QualityStatus.FAILED,
        "frontend.build": QualityStatus.FAILED,
    }
    assert all(result.reason for result in summary.results)


def test_fail_fast_omits_unrelated_tasks_stopped_before_execution() -> None:
    """本地 fail-fast 不把尚未调度的无关叶误报成配置失败。"""
    report = {
        "actions": [
            _action("web:frontend.build", "failed", "task-execution", 9),
            _action("root:backend.contracts", "skipped", "task-execution"),
            _action("root:docs.context", "skipped", "task-execution"),
        ]
    }

    summary = normalize_moon_run(
        report,
        _graph(),
        _definitions(),
        current_os="linux",
        moon_exit_code=1,
        fail_fast=True,
    )

    assert {result.id: result.status for result in summary.results} == {
        "backend.contracts": QualityStatus.BLOCKED,
        "frontend.build": QualityStatus.FAILED,
    }
