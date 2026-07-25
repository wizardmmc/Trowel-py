from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from trowel_py import cli


def test_regenerate_cli_plans_through_shared_service(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    import trowel_py.memory.regeneration as regeneration

    seen: dict[str, object] = {}

    def fake_plan(root: Path, **kwargs):
        seen.update(root=root, **kwargs)
        return SimpleNamespace(to_dict=lambda: {"plan_id": "plan-1"})

    monkeypatch.setattr(regeneration, "plan_regeneration", fake_plan)

    rc = cli._run_memory_cli(
        [
            "regenerate",
            "--layer",
            "daily",
            "--from",
            "2026-07-01",
            "--to",
            "2026-07-07",
            "--mode",
            "stale",
            "--root",
            str(tmp_path),
        ]
    )

    assert rc == 0
    assert seen == {
        "root": tmp_path,
        "layer": "daily",
        "from_period": "2026-07-01",
        "to_period": "2026-07-07",
        "mode": "stale",
    }
    assert json.loads(capsys.readouterr().out)["plan_id"] == "plan-1"


def test_regenerate_cli_run_and_apply_use_shared_service(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    import trowel_py.memory.regeneration as regeneration

    monkeypatch.setattr(
        regeneration,
        "run_regeneration",
        lambda root, plan_id: SimpleNamespace(
            to_dict=lambda: {"run_id": f"run-{plan_id}"}
        ),
    )
    monkeypatch.setattr(
        regeneration,
        "apply_regeneration",
        lambda root, run_id: {"run_id": run_id, "applied": True},
    )

    assert (
        cli._run_memory_cli(
            ["regenerate", "--run", "p1", "--root", str(tmp_path)]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {"run_id": "run-p1"}
    assert (
        cli._run_memory_cli(
            ["regenerate", "--apply", "r1", "--root", str(tmp_path)]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {
        "run_id": "r1",
        "applied": True,
    }
