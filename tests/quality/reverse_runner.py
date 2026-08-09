"""用真实 moon 2.4.6 重跑质量任务图的正向与反向契约。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from scripts.quality.installer import ensure_moon
from scripts.quality.models import QualityStatus
from scripts.quality.report import normalize_moon_run
from scripts.quality.service import current_moon_os

FIXTURE_SOURCE = Path(__file__).resolve().parent / "moon_fixture"


def _moon_json(workspace: Path, executable: Path, *arguments: str) -> dict[str, Any]:
    """运行 fixture workspace 的只读 moon JSON 查询。

    Args:
        workspace: 已初始化独立 Git 仓库的 fixture workspace。
        executable: 经过固定哈希校验的 moon 2.4.6 路径。
        arguments: action graph 或任务定义查询参数。

    Returns:
        moon 标准输出中的 JSON 对象。
    """
    completed = subprocess.run(
        [str(executable), *arguments],
        cwd=workspace,
        env=_isolated_environment(),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _definitions(
    workspace: Path,
    executable: Path,
    graph: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """读取 action graph 中全部 fixture 任务定义。

    Args:
        workspace: 本次自测使用的隔离 workspace。
        executable: 经过固定哈希校验的 moon 2.4.6 路径。
        graph: 本次目标的真实 action graph。

    Returns:
        以完整 target 索引的解析后任务定义。
    """
    return {
        node["params"]["target"]: _moon_json(
            workspace,
            executable,
            "task",
            node["params"]["target"],
            "--json",
        )
        for node in graph["data"].values()
    }


def _isolated_environment() -> dict[str, str]:
    """移除父任务图和 CI provider 变量，并优先使用项目 Python。

    fixture 拥有独立 Git 仓库，不能继承外层 PR 的 base/head；需要 CI 模式的场景由
    ``_execute`` 重新注入通用 ``CI=true``。
    """
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("MOON_", "GITHUB_")) and key != "CI"
    }
    python_bin = str(Path(sys.executable).resolve().parent)
    environment["PATH"] = f"{python_bin}{os.pathsep}{environment.get('PATH', '')}"
    return environment


def _execute(
    workspace: Path,
    executable: Path,
    target: str,
    run_dir: Path,
    run_id: str,
    *,
    fail_build: bool = False,
    keep_going: bool = True,
    ci: bool = False,
) -> tuple[int, dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    """执行一个 fixture target 并返回同一次报告、图和定义。

    Args:
        workspace: 本次自测使用的隔离 workspace。
        executable: 经过固定哈希校验的 moon 2.4.6 路径。
        target: 需要执行的 fixture profile 或叶子 target。
        run_dir: 叶子进程写入受控产物和执行记录的目录。
        run_id: 区分新旧构建产物的本次场景标识。
        fail_build: 是否让 fixture 构建叶按预期失败。
        keep_going: 是否在独立分支失败后继续执行。
        ci: 是否用与正式 CI 相同的 moon 模式执行。

    Returns:
        moon 退出码、本次报告、action graph 和任务定义。
    """
    graph = _moon_json(workspace, executable, "action-graph", target, "--json")
    definitions = _definitions(workspace, executable, graph)
    moon_report = workspace / ".moon" / "cache" / "runReport.json"
    if moon_report.exists():
        moon_report.unlink()
    environment = _isolated_environment()
    environment.update(
        {
            "QUALITY_FIXTURE_RUN_DIR": str(run_dir),
            "QUALITY_FIXTURE_RUN_ID": run_id,
        }
    )
    if fail_build:
        environment["QUALITY_FIXTURE_FAIL_BUILD"] = "1"
    if ci:
        environment["CI"] = "true"
    command = [
        str(executable),
        "exec",
        target,
        "--concurrency",
        "1",
        "--on-failure",
        "continue" if keep_going else "bail",
        "--summary",
        "detailed",
    ]
    if ci:
        command.extend(["--ci", "true"])
    completed = subprocess.run(
        command,
        cwd=workspace,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if not moon_report.is_file():
        raise AssertionError(
            f"moon selftest did not produce report:\n{completed.stdout}{completed.stderr}"
        )
    report = json.loads(moon_report.read_text(encoding="utf-8"))
    return completed.returncode, report, graph, definitions


def _write_stale_artifact(run_dir: Path) -> Path:
    """在正式 contracts 路径放入上一轮产物。

    Args:
        run_dir: 当前反向场景的隔离运行目录。

    Returns:
        用于证明失败构建不会误用旧内容的产物路径。
    """
    artifact = run_dir / "artifacts" / "index.html"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("run_id=old-run\n", encoding="utf-8")
    return artifact


def _verify_reverse(workspace: Path, executable: Path, root: Path) -> None:
    """验证多失败汇总、阻断、平台、cwd 和旧产物不会假绿。

    Args:
        workspace: 本次自测使用的隔离 workspace。
        executable: 经过固定哈希校验的 moon 2.4.6 路径。
        root: 保存本组受控运行产物的临时目录。
    """
    run_dir = root / "reverse"
    stale_artifact = _write_stale_artifact(run_dir)
    code, report, graph, definitions = _execute(
        workspace,
        executable,
        ":gate",
        run_dir,
        "reverse",
        fail_build=True,
    )
    summary = normalize_moon_run(
        report,
        graph,
        definitions,
        current_os=current_moon_os(),
        moon_exit_code=code,
    )
    statuses = {result.id: result.status for result in summary.results}
    assert code == 1
    assert statuses["frontend.build"] is QualityStatus.FAILED
    assert statuses["backend.contracts"] is QualityStatus.BLOCKED
    assert statuses["independent.fail-a"] is QualityStatus.FAILED
    assert statuses["after.fail-a"] is QualityStatus.BLOCKED
    assert statuses["independent.fail-b"] is QualityStatus.FAILED
    assert statuses["independent.pass"] is QualityStatus.PASSED
    assert statuses["environment.missing-command"] is QualityStatus.FAILED
    assert statuses["environment.wrong-cwd"] is QualityStatus.FAILED
    assert statuses["frontend.cwd"] is QualityStatus.PASSED
    platform_statuses = {statuses["platform.linux-only"], statuses["platform.mac-only"]}
    assert platform_statuses == {QualityStatus.PASSED, QualityStatus.NOT_APPLICABLE}
    executed = (run_dir / "executed.txt").read_text(encoding="utf-8").splitlines()
    assert "backend.contracts" not in executed
    assert "after.fail-a" not in executed
    assert "independent.pass" in executed
    assert stale_artifact.read_text(encoding="utf-8") == "run_id=old-run\n"


def _verify_positive(workspace: Path, executable: Path, root: Path) -> None:
    """验证单叶 contracts 会先生成并只接受本次构建产物。

    Args:
        workspace: 本次自测使用的隔离 workspace。
        executable: 经过固定哈希校验的 moon 2.4.6 路径。
        root: 保存本组受控运行产物的临时目录。
    """
    run_dir = root / "positive"
    code, report, graph, definitions = _execute(
        workspace,
        executable,
        ":backend.contracts",
        run_dir,
        "positive",
    )
    summary = normalize_moon_run(
        report,
        graph,
        definitions,
        current_os=current_moon_os(),
        moon_exit_code=code,
    )
    assert code == 0
    assert {result.id: result.status for result in summary.results} == {
        "backend.contracts": QualityStatus.PASSED,
        "frontend.build": QualityStatus.PASSED,
    }
    assert (run_dir / "artifacts" / "index.html").read_text(encoding="utf-8") == (
        "run_id=positive\n"
    )


def _verify_fail_fast(workspace: Path, executable: Path, root: Path) -> None:
    """验证 fail-fast 在第一个失败 action 后停止。

    Args:
        workspace: 本次自测使用的隔离 workspace。
        executable: 经过固定哈希校验的 moon 2.4.6 路径。
        root: 保存本组受控运行产物的临时目录。
    """
    run_dir = root / "fail-fast"
    _write_stale_artifact(run_dir)
    code, report, _graph, _definitions_by_target = _execute(
        workspace,
        executable,
        ":gate",
        run_dir,
        "fail-fast",
        fail_build=True,
        keep_going=False,
    )
    failed_actions = [
        action for action in report["actions"] if action["status"] == "failed"
    ]
    assert code == 1
    assert len(failed_actions) == 1


def _verify_local_and_ci_share_leaf(
    workspace: Path,
    executable: Path,
    root: Path,
) -> None:
    """验证同一坏叶在本地与 CI 模式下都按同一定义失败。

    Args:
        workspace: 本次自测使用的隔离 workspace。
        executable: 经过固定哈希校验的 moon 2.4.6 路径。
        root: 保存本组受控运行产物的临时目录。
    """
    observed_definitions: list[dict[str, Any]] = []
    for mode, ci in (("local", False), ("ci", True)):
        code, report, graph, definitions = _execute(
            workspace,
            executable,
            ":independent.fail-a",
            root / f"shared-{mode}",
            f"shared-{mode}",
            ci=ci,
        )
        summary = normalize_moon_run(
            report,
            graph,
            definitions,
            current_os=current_moon_os(),
            moon_exit_code=code,
        )
        assert code == 1
        assert {result.id: result.status for result in summary.results} == {
            "independent.fail-a": QualityStatus.FAILED
        }
        observed_definitions.append(definitions["root:independent.fail-a"])
    assert observed_definitions[0]["command"] == observed_definitions[1]["command"]


def main() -> int:
    """在临时产物目录运行全部真实 moon 自测。"""
    executable = ensure_moon()
    with tempfile.TemporaryDirectory(prefix="trowel-quality-selftest-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        shutil.copytree(FIXTURE_SOURCE, workspace)
        shutil.rmtree(workspace / ".moon" / "cache", ignore_errors=True)
        subprocess.run(
            ["git", "init", "--initial-branch=main"],
            cwd=workspace,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "add", "."], cwd=workspace, check=True, capture_output=True
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Trowel Quality",
                "-c",
                "user.email=quality@invalid.local",
                "commit",
                "-m",
                "quality selftest fixture",
            ],
            cwd=workspace,
            check=True,
            capture_output=True,
        )
        run_root = root / "runs"
        _verify_reverse(workspace, executable, run_root)
        _verify_positive(workspace, executable, run_root)
        _verify_fail_fast(workspace, executable, run_root)
        _verify_local_and_ci_share_leaf(workspace, executable, run_root)
    print("quality selftest passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
