"""封装 moon 2.4.6 子进程并返回未解释的上游运行事实。"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class MoonExecutionError(RuntimeError):
    """表示 moon 在生成可归一的本次报告前失败。"""


@dataclass(frozen=True)
class RawMoonRun:
    """保存同一次 moon 调用的报告、图、任务定义和状态目录。

    Attributes:
        requested_target: 调用方输入的 profile 或稳定叶子 ID。
        exit_code: ``moon exec`` 的原始退出码。
        report: 本次 ``runReport.json`` 的完整 JSON 对象。
        graph: 执行前读取的原生 action graph。
        definitions: 以完整 target 索引的解析后任务定义。
        state_root: moon 保存各项目叶子 stdout、stderr 和 lastRun 的目录。
    """

    requested_target: str
    exit_code: int
    report: dict[str, Any]
    graph: dict[str, Any]
    definitions: dict[str, dict[str, Any]]
    state_root: Path


class MoonGateway:
    """通过固定 moon 二进制读取任务图并串行执行一个目标。"""

    def __init__(self, *, executable: Path, workspace_root: Path) -> None:
        """绑定 moon 可执行文件和仓库 workspace。

        Args:
            executable: 已通过固定版本哈希校验的 moon 文件。
            workspace_root: 包含 ``.moon/workspace.yml`` 的仓库根目录。
        """
        self._executable = executable
        self._workspace_root = workspace_root

    @property
    def _report_path(self) -> Path:
        """返回 moon 每次调用会覆盖的原始报告路径。"""
        return self._workspace_root / ".moon" / "cache" / "runReport.json"

    def _json_command(self, *arguments: str) -> dict[str, Any]:
        """执行只读 moon 查询并解析 JSON 输出。

        Args:
            arguments: 传给固定 moon 二进制的查询参数，不包含可执行文件路径。

        Returns:
            moon 写到标准输出的 JSON 对象。
        """
        completed = subprocess.run(
            [str(self._executable), *arguments],
            cwd=self._workspace_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise MoonExecutionError(
                f"moon {' '.join(arguments)} failed ({completed.returncode}): "
                f"{completed.stderr.strip()}"
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise MoonExecutionError(
                f"moon {' '.join(arguments)} returned invalid JSON"
            ) from error
        if not isinstance(payload, dict):
            raise MoonExecutionError(
                f"moon {' '.join(arguments)} returned a non-object JSON value"
            )
        return payload

    def _definitions(self, graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """读取 action graph 中每个 target 的解析后任务定义。

        Args:
            graph: 当前调用目标的 moon action graph。

        Returns:
            以完整 moon target 索引的任务定义。
        """
        definitions: dict[str, dict[str, Any]] = {}
        try:
            for node in graph.get("data", {}).values():
                target = node["params"]["target"]
                definitions[target] = self._json_command("task", target, "--json")
        except (AttributeError, KeyError, TypeError) as error:
            raise MoonExecutionError(
                "moon 2.4.6 action graph does not match the expected node schema"
            ) from error
        return definitions

    @property
    def _state_root(self) -> Path:
        """返回 moon 保存每个叶子最近运行证据的共享目录。"""
        return self._workspace_root / ".moon" / "cache" / "states"

    def _clear_previous_task_evidence(
        self,
        definitions: dict[str, dict[str, Any]],
    ) -> None:
        """删除当前任务图各叶子的上一轮日志和状态。

        moon 的状态目录跨调用复用。先清理图中叶子，才能保证本次被阻断或平台不适用的
        任务不会把旧日志复制进新证据目录。

        Args:
            definitions: 当前 action graph 中以完整 target 索引的任务定义。
        """
        for target in definitions:
            project, task = target.split(":", maxsplit=1)
            task_state = self._state_root / project / task
            for name in ("stdout.log", "stderr.log", "lastRun.json"):
                state_file = task_state / name
                if state_file.is_file():
                    state_file.unlink()

    def execute(
        self,
        requested_target: str,
        *,
        keep_going: bool,
        ci: bool,
        console_path: Path,
    ) -> RawMoonRun:
        """串行执行 profile 或单叶并返回同一次调用的原始事实。

        Args:
            requested_target: profile 或不含 moon 项目前缀的稳定叶子 ID。
            keep_going: 是否在独立分支失败后继续执行其它可运行叶子。
            ci: 是否启用 moon CI 行为并向叶子暴露 ``CI=true``。
            console_path: 保存 moon 完整合并输出的位置。

        Returns:
            未做四态解释的 moon 运行事实。
        """
        target = f":{requested_target}"
        graph = self._json_command("action-graph", target, "--json")
        definitions = self._definitions(graph)
        self._clear_previous_task_evidence(definitions)
        if self._report_path.exists():
            self._report_path.unlink()
        command = [
            str(self._executable),
            "exec",
            target,
            "--concurrency",
            "1",
            "--summary",
            "detailed",
            "--on-failure",
            "continue" if keep_going else "bail",
        ]
        if ci:
            command.extend(["--ci", "true"])
        environment = os.environ.copy()
        if ci:
            environment["CI"] = "true"
        console_path.parent.mkdir(parents=True, exist_ok=True)
        with console_path.open("w", encoding="utf-8") as console:
            completed = subprocess.run(
                command,
                cwd=self._workspace_root,
                env=environment,
                stdout=console,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
        if not self._report_path.is_file():
            raise MoonExecutionError(
                f"moon did not produce runReport.json; see {console_path}"
            )
        try:
            report = json.loads(self._report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise MoonExecutionError(
                f"moon produced invalid runReport.json; see {console_path}"
            ) from error
        if not isinstance(report, dict):
            raise MoonExecutionError(
                f"moon produced a non-object runReport.json; see {console_path}"
            )
        return RawMoonRun(
            requested_target=requested_target,
            exit_code=completed.returncode,
            report=report,
            graph=graph,
            definitions=definitions,
            state_root=self._state_root,
        )
