from __future__ import annotations

import json
from pathlib import Path

from trowel_py import cli


def test_memory_tidy_status_prints_watermark_and_pending(
    tmp_path: Path,
    capsys,
) -> None:
    from trowel_py.memory.tidy_state import TidyState, save_state

    save_state(tmp_path, TidyState(weekly_last="2026-W29", monthly_last="2026-06"))

    rc = cli._run_memory_cli(["tidy", "--status", "--root", str(tmp_path)])

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["weekly"]["last_successful"] == "2026-W29"
    assert data["monthly"]["last_successful"] == "2026-06"
    assert isinstance(data["weekly"]["pending"], list)


def test_memory_tidy_status_empty_state_bootstrap(
    tmp_path: Path,
    capsys,
) -> None:
    rc = cli._run_memory_cli(["tidy", "--status", "--root", str(tmp_path)])

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["weekly"]["last_successful"] is None
    assert len(data["weekly"]["pending"]) == 1


def test_memory_tidy_catchup_requires_from_and_scope(
    tmp_path: Path,
    capsys,
) -> None:
    rc = cli._run_memory_cli(["tidy", "--catchup", "--root", str(tmp_path)])

    assert rc == 2
    assert "--from" in capsys.readouterr().out


def test_memory_tidy_catchup_runs_explicit_range(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    import trowel_py.memory.tidy_scheduler as scheduler_module

    seen: dict[str, object] = {}

    def fake(root, scope, from_period, provider_factory, *, now=None):
        seen["root"] = str(root)
        seen["scope"] = scope
        seen["from"] = from_period
        assert callable(provider_factory)
        return {
            "scope": scope,
            "from": from_period,
            "planned": [from_period],
            "ran": [from_period],
            "failed_at": None,
            "watermark": from_period,
        }

    monkeypatch.setattr(scheduler_module, "run_explicit_catchup", fake)

    rc = cli._run_memory_cli(
        [
            "tidy",
            "--catchup",
            "--from",
            "2026-W20",
            "--scope",
            "weekly",
            "--root",
            str(tmp_path),
        ]
    )

    assert rc == 0
    assert seen == {"root": str(tmp_path), "scope": "weekly", "from": "2026-W20"}
    assert json.loads(capsys.readouterr().out)["from"] == "2026-W20"


def test_tidy_rollback_uses_facade_dictionary_patch(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    import trowel_py.memory.tidy as tidy_module

    rolled_back: list[tuple[Path, str]] = []
    converged: list[Path] = []
    monkeypatch.setattr(
        tidy_module,
        "rollback_plan",
        lambda root, plan_id: rolled_back.append((root, plan_id)),
    )
    monkeypatch.setattr(
        cli,
        "_ensure_dict_after_batch",
        lambda root: converged.append(root),
    )

    rc = cli._run_memory_cli(["tidy", "--rollback", "plan-1", "--root", str(tmp_path)])

    assert rc == 0
    assert rolled_back == [(tmp_path, "plan-1")]
    assert converged == [tmp_path]
    assert "plan-1 restored" in capsys.readouterr().out
