"""把 moon 原始事实和每叶日志复制到一次运行独占的稳定证据目录。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from scripts.quality.models import RunSummary
from scripts.quality.moon import RawMoonRun


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """以稳定缩进写入 UTF-8 JSON。

    Args:
        path: 需要创建或覆盖的 JSON 文件路径。
        payload: 只包含可 JSON 序列化值的证据对象。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


class EvidenceStore:
    """将一次 Gate 的原始报告、摘要和叶子证据发布到指定目录。"""

    def __init__(self, run_dir: Path) -> None:
        """绑定本次运行独占的证据目录。

        Args:
            run_dir: 本次调用的控制台、JSON 与叶子日志共同根目录。
        """
        self._run_dir = run_dir

    def _copy_leaf_logs(self, raw_run: RawMoonRun, summary: RunSummary) -> None:
        """复制 moon 状态日志，并为未启动叶子写入同形态结果证据。

        Args:
            raw_run: 提供共享 moon 状态目录的本次原始运行。
            summary: 决定稳定叶子 ID、证据目录和结果内容的归一摘要。
        """
        for result in summary.results:
            destination = self._run_dir / result.evidence
            destination.mkdir(parents=True, exist_ok=True)
            project, task = result.target.split(":", maxsplit=1)
            source = raw_run.state_root / project / task
            for name in ("stdout.log", "stderr.log", "lastRun.json"):
                source_file = source / name
                if source_file.is_file():
                    shutil.copy2(source_file, destination / name)
            _write_json(destination / "result.json", result.to_dict())

    def persist_raw(self, raw_run: RawMoonRun) -> None:
        """在归一前发布诊断报告所需的原始 moon 事实。

        Args:
            raw_run: 同一次 moon 调用产生的原始报告、图和定义。
        """
        self._run_dir.mkdir(parents=True, exist_ok=True)
        _write_json(self._run_dir / "moon-run-report.json", raw_run.report)
        _write_json(self._run_dir / "moon-action-graph.json", raw_run.graph)
        _write_json(self._run_dir / "moon-task-definitions.json", raw_run.definitions)

    def persist_summary(self, raw_run: RawMoonRun, summary: RunSummary) -> Path:
        """发布四态摘要和每叶证据。

        原始事实由 ``persist_raw`` 先行保存，因此报告归一失败也不会丢失上游证据。

        Args:
            raw_run: 提供本次逐叶状态日志的原始运行。
            summary: 由这些原始事实归一得到的稳定摘要。

        Returns:
            最终机器摘要路径。
        """
        self._copy_leaf_logs(raw_run, summary)
        summary_path = self._run_dir / "summary.json"
        _write_json(summary_path, summary.to_dict())
        return summary_path
