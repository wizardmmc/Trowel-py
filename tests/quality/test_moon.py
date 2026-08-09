"""验证 moon gateway 的共享状态清理边界。"""

from __future__ import annotations

from pathlib import Path

from scripts.quality.moon import MoonGateway


def test_gateway_clears_previous_leaf_evidence_before_execution(tmp_path: Path) -> None:
    """本轮被阻断或不适用的叶子不得继承上一轮日志。"""
    gateway = MoonGateway(executable=tmp_path / "moon", workspace_root=tmp_path)
    task_state = tmp_path / ".moon" / "cache" / "states" / "web" / "frontend.build"
    task_state.mkdir(parents=True)
    for name in ("stdout.log", "stderr.log", "lastRun.json"):
        task_state.joinpath(name).write_text("stale\n", encoding="utf-8")
    task_state.joinpath("unrelated.json").write_text("keep\n", encoding="utf-8")

    gateway._clear_previous_task_evidence(  # noqa: SLF001 - 直接验证证据新鲜度边界。
        {"web:frontend.build": {"id": "frontend.build"}}
    )

    assert not task_state.joinpath("stdout.log").exists()
    assert not task_state.joinpath("stderr.log").exists()
    assert not task_state.joinpath("lastRun.json").exists()
    assert task_state.joinpath("unrelated.json").is_file()
