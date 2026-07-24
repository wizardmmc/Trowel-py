from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from trowel_py import cli
from trowel_py.memory.hooks import HookRegistry


def test_run_memory_tidy_dispatches_registered_job(tmp_path: Path, capsys) -> None:
    registry = HookRegistry()
    called: list[str] = []
    registry.register_tidy_job(lambda event: called.append(event["root"]))

    rc = cli._run_memory_tidy(registry, tmp_path)

    assert rc == 0
    assert called == [str(tmp_path)]
    assert "tidy" in capsys.readouterr().out


def test_run_memory_tidy_empty_registry_is_noop(tmp_path: Path, capsys) -> None:
    rc = cli._run_memory_tidy(HookRegistry(), tmp_path)

    assert rc == 0
    assert "registered jobs: 0" in capsys.readouterr().out


def test_memory_cli_routes_tidy(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake(registry: object, root: Path) -> int:
        seen["root"] = str(root)
        return 0

    monkeypatch.setattr(cli, "_run_memory_tidy", fake)

    rc = cli._run_memory_cli(["tidy", "--root", str(tmp_path)])

    assert rc == 0
    assert seen["root"] == str(tmp_path)


def test_main_intercepts_memory_subcommand(monkeypatch) -> None:
    monkeypatch.setattr(cli.sys, "argv", ["trowel-py", "memory", "tidy"])
    routed: list[list[str]] = []
    monkeypatch.setattr(cli, "_run_memory_cli", lambda argv: routed.append(argv) or 0)

    try:
        cli.main()
    except SystemExit as exc:
        assert exc.code == 0

    assert routed == [["tidy"]]


def test_memory_review_dispatches_write_job(tmp_path: Path, capsys) -> None:
    rc = cli._run_memory_review(HookRegistry(), tmp_path, "2026-07-09")

    assert rc == 0
    output = capsys.readouterr().out
    assert "review" in output
    assert "2026-07-09" in output


def test_memory_metrics_prints_usage_indicators(tmp_path: Path, capsys) -> None:
    rc = cli._run_memory_cli(["metrics", "--root", str(tmp_path)])

    assert rc == 0
    output = capsys.readouterr().out
    assert "north_star" in output
    assert "usage" in output
    assert "read_rate" in output
    assert "hit_quality" in output
    assert "recall_miss_rate" in output


def test_memory_promotion_dry_run_prints_policy_and_gaps(
    tmp_path: Path,
    capsys,
) -> None:
    rc = cli._run_memory_cli(["promotion", "--root", str(tmp_path)])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["dry_run"] is True
    assert report["policy"]["min_helpful_sessions"] == 3
    assert "gaps" in report


def test_memory_promotion_policy_override_from_file(
    tmp_path: Path,
    capsys,
) -> None:
    from trowel_py.memory.promotion_policy import PromotionPolicy, save_policy

    policy_path = tmp_path / "policy.json"
    save_policy(PromotionPolicy(min_helpful_sessions=1), policy_path)

    rc = cli._run_memory_cli(
        ["promotion", "--policy", str(policy_path), "--root", str(tmp_path)]
    )

    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["policy"]["min_helpful_sessions"] == 1


def test_memory_cli_routes_review(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake(registry: object, root: Path, date_str: str) -> int:
        seen["date"] = date_str
        seen["root"] = str(root)
        return 0

    monkeypatch.setattr(cli, "_run_memory_review", fake)

    rc = cli._run_memory_cli(
        ["review", "--date", "2026-07-09", "--root", str(tmp_path)]
    )

    assert rc == 0
    assert seen["date"] == "2026-07-09"
    assert seen["root"] == str(tmp_path)


def test_memory_review_default_date_today(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake(registry: object, root: Path, date_str: str) -> int:
        seen["date"] = date_str
        return 0

    monkeypatch.setattr(cli, "_run_memory_review", fake)

    cli._run_memory_cli(["review", "--root", str(tmp_path)])

    assert seen["date"] == date.today().isoformat()


def test_memory_cli_uses_facade_period_patch(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import trowel_py.config as config_module
    import trowel_py.llm.client as client_module
    import trowel_py.memory.tidy as tidy_module

    seen: dict[str, str] = {}

    class FakeProvider:
        def __init__(self, config: object) -> None:
            pass

    def fake_weekly(root: Path, iso_week: str, provider: object) -> dict[str, str]:
        seen["iso_week"] = iso_week
        return {"period": iso_week}

    monkeypatch.setattr(cli, "_current_iso_week", lambda: "2099-W01")
    monkeypatch.setattr(config_module, "load_llm_config", lambda: object())
    monkeypatch.setattr(client_module, "AnthropicProvider", FakeProvider)
    monkeypatch.setattr(tidy_module, "run_weekly_tidy", fake_weekly)

    rc = cli._run_memory_cli(["tidy", "--weekly", "--root", str(tmp_path)])

    assert rc == 0
    assert seen["iso_week"] == "2099-W01"
    assert json.loads(capsys.readouterr().out) == {"period": "2099-W01"}


def test_migrate_apply_uses_facade_dictionary_patch(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from types import SimpleNamespace

    import trowel_py.memory.migrate as migrate_module

    converged: list[Path] = []
    monkeypatch.setattr(
        migrate_module,
        "migrate_memory",
        lambda root, *, apply: SimpleNamespace(
            scanned=0,
            migrated=0,
            skipped=0,
            backed_up=None,
        ),
    )
    monkeypatch.setattr(
        cli,
        "_ensure_dict_after_batch",
        lambda root: converged.append(root),
    )

    rc = cli._run_memory_cli(["migrate", "--apply", "--root", str(tmp_path)])

    assert rc == 0
    assert converged == [tmp_path]
    assert "migrate apply" in capsys.readouterr().out
