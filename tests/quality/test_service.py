"""验证 service 只组合 gateway、归一、锁和证据存储。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.quality.models import QualityStatus
from scripts.quality.moon import RawMoonRun
from scripts.quality.service import (
    QualityReportError,
    QualityRunRequest,
    QualityRunService,
)


class FakeGateway:
    """返回固定原始事实的最小 gateway 替身。"""

    def __init__(self, state_root: Path) -> None:
        """保存测试使用的 moon 状态目录。"""
        self._state_root = state_root

    def execute(
        self,
        requested_target: str,
        *,
        keep_going: bool,
        ci: bool,
        console_path: Path,
    ) -> RawMoonRun:
        """返回一个通过叶子，并模拟 moon 已写入控制台和叶子日志。"""
        assert requested_target == "docs.context"
        assert keep_going is True
        assert ci is False
        console_path.parent.mkdir(parents=True, exist_ok=True)
        console_path.write_text("moon console\n", encoding="utf-8")
        state_dir = self._state_root / "root" / "docs.context"
        state_dir.mkdir(parents=True)
        state_dir.joinpath("stdout.log").write_text("findings=0\n", encoding="utf-8")
        state_dir.joinpath("stderr.log").write_text("", encoding="utf-8")
        return RawMoonRun(
            requested_target=requested_target,
            exit_code=0,
            report={
                "actions": [
                    {
                        "duration": {"secs": 0, "nanos": 1_000_000},
                        "node": {"params": {"target": "root:docs.context"}},
                        "operations": [
                            {
                                "status": "passed",
                                "meta": {"type": "task-execution", "exitCode": 0},
                            }
                        ],
                        "status": "passed",
                    }
                ]
            },
            graph={"graph": {"edges": []}, "data": {}},
            definitions={
                "root:docs.context": {
                    "id": "docs.context",
                    "description": "检查公共项目上下文。",
                    "options": {"cache": False},
                }
            },
            state_root=self._state_root,
        )


class MalformedGateway:
    """返回字段缺失报告的 gateway 替身。"""

    def __init__(self, state_root: Path) -> None:
        """保存测试使用的 moon 状态目录。

        Args:
            state_root: 原始运行对象引用的隔离状态根目录。
        """
        self._state_root = state_root

    def execute(
        self,
        requested_target: str,
        *,
        keep_going: bool,
        ci: bool,
        console_path: Path,
    ) -> RawMoonRun:
        """返回能解析但缺少 action node 的 moon 报告。

        Args:
            requested_target: 测试调用的稳定叶子 ID。
            keep_going: service 传入的继续执行选择。
            ci: service 传入的 CI 模式。
            console_path: 本次测试控制台证据路径。

        Returns:
            缺少固定版本必需字段的原始运行对象。
        """
        del keep_going, ci
        console_path.parent.mkdir(parents=True, exist_ok=True)
        console_path.write_text("malformed report\n", encoding="utf-8")
        return RawMoonRun(
            requested_target=requested_target,
            exit_code=1,
            report={"actions": [{}]},
            graph={"graph": {"edges": []}, "data": {}},
            definitions={},
            state_root=self._state_root,
        )


def test_service_persists_raw_facts_summary_and_leaf_logs(tmp_path: Path) -> None:
    """service 在释放 workspace 锁前写完同一次调用的全部证据。"""
    workspace = tmp_path / "workspace"
    run_dir = tmp_path / "run"
    service = QualityRunService(
        gateway=FakeGateway(tmp_path / "states"),
        workspace_root=workspace,
        current_os="linux",
    )

    summary = service.run(QualityRunRequest(target="docs.context", run_dir=run_dir))

    assert summary.results[0].status is QualityStatus.PASSED
    assert (
        json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))["exit_code"]
        == 0
    )
    assert (run_dir / "moon-run-report.json").is_file()
    assert (run_dir / "moon-action-graph.json").is_file()
    assert (run_dir / "moon-task-definitions.json").is_file()
    assert (run_dir / "logs" / "docs.context" / "stdout.log").read_text(
        encoding="utf-8"
    ) == ("findings=0\n")
    assert (run_dir / "logs" / "docs.context" / "result.json").is_file()


def test_service_rejects_nonempty_evidence_directory(tmp_path: Path) -> None:
    """显式复用旧目录时不得混合两次 Gate 证据。"""
    run_dir = tmp_path / "old-run"
    run_dir.mkdir()
    run_dir.joinpath("summary.json").write_text("{}\n", encoding="utf-8")
    service = QualityRunService(
        gateway=FakeGateway(tmp_path / "states"),
        workspace_root=tmp_path / "workspace",
        current_os="linux",
    )

    with pytest.raises(FileExistsError, match="not empty"):
        service.run(QualityRunRequest(target="docs.context", run_dir=run_dir))


def test_service_rejects_file_as_evidence_directory(tmp_path: Path) -> None:
    """输出路径为普通文件时必须返回稳定参数错误而不是二次 traceback。"""
    output_file = tmp_path / "evidence.json"
    output_file.write_text("{}\n", encoding="utf-8")
    service = QualityRunService(
        gateway=FakeGateway(tmp_path / "states"),
        workspace_root=tmp_path / "workspace",
        current_os="linux",
    )

    with pytest.raises(FileExistsError, match="not a directory"):
        service.run(QualityRunRequest(target="docs.context", run_dir=output_file))


def test_service_rechecks_evidence_directory_after_acquiring_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """等待同 workspace 的前一轮结束后必须拒绝混入其证据。"""
    run_dir = tmp_path / "shared-run"
    run_dir.mkdir()
    service = QualityRunService(
        gateway=FakeGateway(tmp_path / "states"),
        workspace_root=tmp_path / "workspace",
        current_os="linux",
    )

    def enter_with_completed_run(lock: object) -> object:
        """模拟等待锁期间另一进程已经发布摘要。"""
        run_dir.joinpath("summary.json").write_text("{}\n", encoding="utf-8")
        return lock

    monkeypatch.setattr(
        "scripts.quality.service.QualityRunLock.__enter__",
        enter_with_completed_run,
    )

    with pytest.raises(FileExistsError, match="not empty"):
        service.run(QualityRunRequest(target="docs.context", run_dir=run_dir))


def test_service_maps_malformed_moon_shape_to_runner_error(tmp_path: Path) -> None:
    """字段缺失的 moon JSON 必须成为 CLI 可留证的稳定 RuntimeError。"""
    service = QualityRunService(
        gateway=MalformedGateway(tmp_path / "states"),
        workspace_root=tmp_path / "workspace",
        current_os="linux",
    )

    with pytest.raises(QualityReportError, match="expected action schema"):
        service.run(QualityRunRequest(target="docs.context", run_dir=tmp_path / "run"))

    assert (tmp_path / "run" / "moon-run-report.json").is_file()
    assert (tmp_path / "run" / "moon-action-graph.json").is_file()
    assert (tmp_path / "run" / "moon-task-definitions.json").is_file()
