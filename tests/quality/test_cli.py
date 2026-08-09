"""验证质量 CLI 在 runner 边界失败时仍发布稳定证据。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.quality.cli import main
from scripts.quality.service import QualityReportError, QualityRunRequest


def test_cli_persists_report_schema_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """字段契约漂移不得只打印 Python traceback。

    Args:
        tmp_path: 保存本次 CLI 失败证据的隔离目录。
        monkeypatch: 替换安装和 service 边界，避免启动真实 moon。
    """

    def prepared_moon() -> Path:
        """返回无需真实执行的测试 moon 路径。"""
        return tmp_path / "moon"

    def raise_schema_error(_service: object, request: QualityRunRequest) -> None:
        """模拟归一层发现固定版本报告字段漂移。

        Args:
            _service: CLI 创建的 service 实例，本场景不读取其依赖。
            request: CLI 传给 service 的目标和证据目录。
        """
        assert request.target == "gate"
        raise QualityReportError("unexpected moon action schema")

    monkeypatch.setattr("scripts.quality.cli.ensure_moon", prepared_moon)
    monkeypatch.setattr("scripts.quality.cli.QualityRunService.run", raise_schema_error)
    run_dir = tmp_path / "run"

    exit_code = main(["gate", "--output", str(run_dir)])

    evidence = json.loads(
        run_dir.joinpath("runner-error.json").read_text(encoding="utf-8")
    )
    assert exit_code == 2
    assert evidence == {
        "status": "failed",
        "reason": "unexpected moon action schema",
    }


def test_cli_rejects_output_file_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """普通文件不能作为证据目录，且 CLI 必须稳定返回参数错误。

    Args:
        tmp_path: 创建无风险输出文件的隔离目录。
        monkeypatch: 替换安装边界，保证参数测试不访问网络。
    """

    def prepared_moon() -> Path:
        """返回无需下载或执行的测试 moon 路径。"""
        return tmp_path / "moon"

    def reject_output_file(_service: object, request: QualityRunRequest) -> None:
        """模拟 service 已确认输出路径不是目录。

        Args:
            _service: CLI 创建的 service 实例，本场景不读取其依赖。
            request: CLI 传给 service 的目标和输出路径。
        """
        assert request.run_dir == output_file
        raise FileExistsError(
            f"quality evidence path is not a directory: {output_file}"
        )

    monkeypatch.setattr("scripts.quality.cli.ensure_moon", prepared_moon)
    output_file = tmp_path / "existing.json"
    output_file.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr("scripts.quality.cli.QualityRunService.run", reject_output_file)

    exit_code = main(["gate", "--output", str(output_file)])

    assert exit_code == 2
    assert output_file.read_text(encoding="utf-8") == "{}\n"
