"""tests for the `trowel-py memory tidy` CLI (slice-038 T5)."""
from __future__ import annotations

from pathlib import Path

from trowel_py import cli
from trowel_py.memory.hooks import HookRegistry


def test_run_memory_tidy_dispatches_registered_job(tmp_path: Path, capsys) -> None:
    reg = HookRegistry()
    called: list[str] = []
    reg.register_tidy_job(lambda ev: called.append(ev["root"]))
    rc = cli._run_memory_tidy(reg, tmp_path)
    assert rc == 0
    assert called == [str(tmp_path)]
    out = capsys.readouterr().out
    assert "tidy" in out


def test_run_memory_tidy_empty_registry_is_noop(tmp_path: Path, capsys) -> None:
    # 空跑: no jobs registered in 038 -> dispatch must not error.
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
    # bare `trowel-py memory tidy ...` must route to memory, never reach uvicorn.
    monkeypatch.setattr(cli.sys, "argv", ["trowel-py", "memory", "tidy"])
    routed: list[list[str]] = []
    monkeypatch.setattr(cli, "_run_memory_cli", lambda argv: routed.append(argv) or 0)
    try:
        cli.main()
    except SystemExit as e:
        assert e.code == 0
    assert routed == [["tidy"]]


# ---------- slice-040 T12: `trowel-py memory review` ----------


def test_memory_review_dispatches_write_job(tmp_path: Path, capsys) -> None:
    # dispatches the write loop over root for date_str. With no sessions
    # registered, run_daily_review is a no-op (find_pending empty).
    rc = cli._run_memory_review(HookRegistry(), tmp_path, "2026-07-09")
    assert rc == 0
    out = capsys.readouterr().out
    assert "review" in out
    assert "2026-07-09" in out


def test_memory_cli_routes_review(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake(registry: object, root: Path, date_str: str) -> int:
        seen["date"] = date_str
        seen["root"] = str(root)
        return 0

    monkeypatch.setattr(cli, "_run_memory_review", fake)
    rc = cli._run_memory_cli(["review", "--date", "2026-07-09", "--root", str(tmp_path)])
    assert rc == 0
    assert seen["date"] == "2026-07-09"
    assert seen["root"] == str(tmp_path)


def test_memory_review_default_date_today(tmp_path: Path, monkeypatch) -> None:
    from datetime import date

    seen: dict[str, str] = {}

    def fake(registry: object, root: Path, date_str: str) -> int:
        seen["date"] = date_str
        return 0

    monkeypatch.setattr(cli, "_run_memory_review", fake)
    cli._run_memory_cli(["review", "--root", str(tmp_path)])
    assert seen["date"] == date.today().isoformat()
