"""把 moon 原始 action、任务定义和依赖图转换成四态摘要。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from scripts.quality.models import QualityResult, QualityStatus, RunSummary

PROFILE_IDS = frozenset({"gate", "gate-full"})


def _duration_seconds(action: Mapping[str, Any]) -> float:
    """把 moon 的秒和纳秒耗时转换为秒。

    Args:
        action: moon 报告中的单个任务 action。

    Returns:
        保留四位小数的叶子耗时秒数。
    """
    duration = action.get("duration") or {}
    return round(
        float(duration.get("secs", 0))
        + float(duration.get("nanos", 0)) / 1_000_000_000,
        4,
    )


def _operation_types(action: Mapping[str, Any]) -> set[str]:
    """返回 action 中所有 operation 类型。

    Args:
        action: moon 报告中的单个任务 action。

    Returns:
        用于区分真实执行、缓存恢复和 no-op 的 operation 类型集合。
    """
    return {
        operation.get("meta", {}).get("type", "")
        for operation in action.get("operations", [])
    }


def _exit_code(action: Mapping[str, Any]) -> int | None:
    """返回任务执行或缓存恢复 operation 记录的退出码。

    Args:
        action: moon 报告中的单个任务 action。

    Returns:
        最后一个含退出码 operation 的整数值；任务未启动时为 None。
    """
    for operation in reversed(action.get("operations", [])):
        exit_code = operation.get("meta", {}).get("exitCode")
        if isinstance(exit_code, int):
            return exit_code
    return None


def _dependencies(graph: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    """按 moon action-graph 的 dependent→dependency 边建立依赖索引。

    Args:
        graph: 与本次执行目标对应的 moon action graph。

    Returns:
        以完整 target 索引的直接依赖 target 列表。
    """
    data = graph.get("data", {})
    by_target: dict[str, list[str]] = {}
    for dependent_index, dependency_index, _kind in graph.get("graph", {}).get(
        "edges", []
    ):
        dependent = data[str(dependent_index)]["params"]["target"]
        dependency = data[str(dependency_index)]["params"]["target"]
        by_target.setdefault(dependent, []).append(dependency)
    return {target: tuple(values) for target, values in by_target.items()}


def _platform_reason(definition: Mapping[str, Any], current_os: str) -> str | None:
    """平台明确不匹配时返回不适用原因。

    Args:
        definition: moon 解析后的单个任务定义。
        current_os: moon 使用的平台名。

    Returns:
        平台永久不适用的短原因；声明匹配或未声明平台时为 None。
    """
    allowed_os = definition.get("options", {}).get("os") or []
    if allowed_os and current_os not in allowed_os:
        return f"requires {','.join(allowed_os)}; current platform is {current_os}"
    return None


def _base_result(
    target: str,
    action: Mapping[str, Any],
    definition: Mapping[str, Any],
    current_os: str,
) -> QualityResult | None:
    """转换不依赖其它叶子状态即可确定的 action。

    Args:
        target: action 对应的完整 moon target。
        action: moon 报告中的单个任务 action。
        definition: 与 target 对应的解析后任务定义。
        current_os: moon 使用的平台名。

    Returns:
        已确定的四态结果；聚合入口或待解释的 skipped action 返回 None。
    """
    task_id = str(definition["id"])
    if task_id in PROFILE_IDS:
        return None
    status = action.get("status")
    operations = _operation_types(action)
    reason: str | None = None
    normalized: QualityStatus | None = None
    if status == "cached":
        normalized = QualityStatus.FAILED
        reason = "quality tasks must not reuse cached success"
    elif "no-operation" in operations:
        reason = _platform_reason(definition, current_os)
        normalized = QualityStatus.NOT_APPLICABLE if reason else QualityStatus.FAILED
        if reason is None:
            reason = (
                "moon returned no-operation without a mismatched options.os declaration"
            )
    elif status == "passed":
        normalized = QualityStatus.PASSED
    elif status == "failed":
        normalized = QualityStatus.FAILED
        code = _exit_code(action)
        reason = (
            f"process exited {code}"
            if code is not None
            else "moon reported task failure"
        )
    elif status != "skipped":
        normalized = QualityStatus.FAILED
        reason = f"unsupported moon action status: {status}"
    if normalized is None:
        return None
    return QualityResult(
        id=task_id,
        target=target,
        status=normalized,
        duration_seconds=_duration_seconds(action),
        exit_code=_exit_code(action),
        reason=reason,
        hint=str(definition.get("description") or "查看叶子日志"),
        evidence=f"logs/{task_id}",
    )


def _blocked_result(
    target: str,
    action: Mapping[str, Any],
    definition: Mapping[str, Any],
    blocked_by: tuple[str, ...],
    definitions: Mapping[str, Mapping[str, Any]],
) -> QualityResult:
    """生成依赖失败后未启动叶子的阻断结果。

    Args:
        target: 被阻断叶子的完整 moon target。
        action: moon 报告中该叶子的 skipped action。
        definition: 被阻断叶子的解析后任务定义。
        blocked_by: 已失败或被阻断的直接依赖 target。
        definitions: 当前 action graph 的全部任务定义。

    Returns:
        带直接阻断来源的稳定结果。
    """
    blocked_ids = [str(definitions[dependency]["id"]) for dependency in blocked_by]
    task_id = str(definition["id"])
    return QualityResult(
        id=task_id,
        target=target,
        status=QualityStatus.BLOCKED,
        duration_seconds=_duration_seconds(action),
        exit_code=None,
        reason=f"blocked by: {', '.join(sorted(blocked_ids))}",
        hint=str(definition.get("description") or "先修复失败依赖"),
        evidence=f"logs/{task_id}",
    )


def _unexpected_skip_result(
    target: str,
    action: Mapping[str, Any],
    definition: Mapping[str, Any],
) -> QualityResult:
    """把无法由失败依赖解释的 skipped 视为配置失败。

    Args:
        target: 无法解释跳过原因的完整 moon target。
        action: moon 报告中的 skipped action。
        definition: 与 target 对应的解析后任务定义。

    Returns:
        提醒维护者检查任务条件的失败结果。
    """
    task_id = str(definition["id"])
    return QualityResult(
        id=task_id,
        target=target,
        status=QualityStatus.FAILED,
        duration_seconds=_duration_seconds(action),
        exit_code=None,
        reason="moon skipped task without a failed dependency",
        hint=str(definition.get("description") or "检查 moon 任务条件"),
        evidence=f"logs/{task_id}",
    )


def normalize_moon_run(
    report: Mapping[str, Any],
    graph: Mapping[str, Any],
    definitions: Mapping[str, Mapping[str, Any]],
    *,
    current_os: str,
    requested_target: str = "gate",
    moon_exit_code: int = 0,
    fail_fast: bool = False,
) -> RunSummary:
    """将 moon 2.4.6 原始事实转换为稳定四态摘要。

    Args:
        report: moon 本次生成的 ``runReport.json``。
        graph: 同一 target 的 ``action-graph --json`` 结果。
        definitions: 以完整 moon target 索引的 ``moon task --json`` 结果。
        current_os: moon 使用的 ``linux``、``macos`` 或 ``windows`` 平台名。
        requested_target: 调用方输入的 profile 或叶子 ID。
        moon_exit_code: moon exec 的原始退出码。
        fail_fast: 是否省略因本地调试提前停止且没有失败依赖的叶子。

    Returns:
        按稳定 ID 排序的四态摘要。
    """
    actions = {
        action["node"]["params"]["target"]: action
        for action in report.get("actions", [])
    }
    dependencies = _dependencies(graph)
    results: dict[str, QualityResult] = {}
    skipped: set[str] = set()
    for target, action in actions.items():
        definition = definitions[target]
        result = _base_result(target, action, definition, current_os)
        if result is None:
            if definition["id"] not in PROFILE_IDS:
                skipped.add(target)
            continue
        results[target] = result

    while skipped:
        changed = False
        for target in skipped.copy():
            blocked_by = tuple(
                dependency
                for dependency in dependencies.get(target, ())
                if dependency in results
                and results[dependency].status
                in {QualityStatus.FAILED, QualityStatus.BLOCKED}
            )
            if not blocked_by:
                continue
            results[target] = _blocked_result(
                target,
                actions[target],
                definitions[target],
                blocked_by,
                definitions,
            )
            skipped.remove(target)
            changed = True
        if not changed:
            if not fail_fast:
                for target in skipped:
                    results[target] = _unexpected_skip_result(
                        target,
                        actions[target],
                        definitions[target],
                    )
            break

    ordered = tuple(sorted(results.values(), key=lambda result: result.id))
    has_explained_failure = any(
        result.status in {QualityStatus.FAILED, QualityStatus.BLOCKED}
        for result in ordered
    )
    runner_error = None
    if moon_exit_code != 0 and not has_explained_failure:
        runner_error = f"moon exited {moon_exit_code} without a failed or blocked leaf"
    return RunSummary(
        requested_target=requested_target,
        moon_exit_code=moon_exit_code,
        results=ordered,
        runner_error=runner_error,
    )
