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


def test_memory_metrics_prints_usage_indicators(tmp_path: Path, capsys) -> None:
    """slice-053: `trowel memory metrics` prints the three usage indicators
    (read_rate / hit_quality / recall_miss_rate) alongside the north-star block."""
    rc = cli._run_memory_cli(["metrics", "--root", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "north_star" in out
    assert "usage" in out
    assert "read_rate" in out
    assert "hit_quality" in out
    assert "recall_miss_rate" in out


def test_memory_promotion_dry_run_prints_policy_and_gaps(
    tmp_path: Path, capsys
) -> None:
    """slice-065: `trowel memory promotion` prints a dry-run gap report and
    the policy in force (writes nothing)."""
    import json

    rc = cli._run_memory_cli(["promotion", "--root", str(tmp_path)])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["dry_run"] is True
    assert report["policy"]["min_helpful_sessions"] == 3
    assert "gaps" in report


def test_memory_promotion_policy_override_from_file(
    tmp_path: Path, capsys
) -> None:
    """--policy loads a PromotionPolicy JSON override."""
    import json

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


# ---------- slice-040-a: `trowel-py memory repair` ----------


def test_memory_cli_routes_repair_dry_run(tmp_path: Path, capsys) -> None:
    # bare dry-run over an empty root prints the plan + exit 0 (no writes).
    rc = cli._run_memory_cli(
        ["repair", "--date", "2026-07-09", "--root", str(tmp_path)]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "2026-07-09" in out
    assert not (tmp_path / "episodes").exists()


def test_memory_cli_routes_repair_apply(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake(root: Path, date_str: str, *, apply: bool) -> int:
        seen["date"] = date_str
        seen["root"] = str(root)
        seen["apply"] = apply
        return 0

    monkeypatch.setattr(cli, "_run_repair", fake)
    rc = cli._run_memory_cli(
        ["repair", "--date", "2026-07-09", "--apply", "--root", str(tmp_path)]
    )
    assert rc == 0
    assert seen == {"date": "2026-07-09", "root": str(tmp_path), "apply": True}


# ---------- slice-040-b: `trowel-py memory backfill-completed` ----------


def _seed_session(mem: Path, sid: str, jsonl_path: str) -> None:
    """Register one legacy session (no completed water mark) into tmp sessions.db."""
    from trowel_py.memory.sessions_repo import (
        SessionRecord,
        create_sessions_repository,
        open_sessions_db,
    )

    conn = open_sessions_db(mem)
    try:
        repo = create_sessions_repository(conn)
        repo.register(
            SessionRecord(
                cc_session_id=sid,
                workdir="/proj",
                date="2026-07-09",
                jsonl_path=jsonl_path,
                registered_at="2026-07-09T10:00:00",
            )
        )
    finally:
        conn.close()


def _completed_offset(mem: Path, sid: str) -> int | None:
    from trowel_py.memory.sessions_repo import (
        create_sessions_repository,
        open_sessions_db,
    )

    conn = open_sessions_db(mem)
    try:
        for rec in create_sessions_repository(conn).find_by_date("2026-07-09"):
            if rec.cc_session_id == sid:
                return rec.last_completed_offset
    finally:
        conn.close()
    return None


def test_backfill_completed_dry_run(tmp_path: Path, capsys) -> None:
    mem = tmp_path / "memory"
    mem.mkdir()
    jsonl = tmp_path / "s.jsonl"
    jsonl.write_text("x" * 500)
    _seed_session(mem, "s1", str(jsonl))

    rc = cli._run_backfill_completed(mem, "2026-07-09", apply=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "500" in out
    # dry-run does not write
    assert _completed_offset(mem, "s1") is None


def test_backfill_completed_apply(tmp_path: Path, capsys) -> None:
    mem = tmp_path / "memory"
    mem.mkdir()
    jsonl = tmp_path / "s.jsonl"
    jsonl.write_text("x" * 500)
    _seed_session(mem, "s1", str(jsonl))

    rc = cli._run_backfill_completed(mem, "2026-07-09", apply=True)
    assert rc == 0
    out = capsys.readouterr().out
    assert "APPLY" in out
    assert _completed_offset(mem, "s1") == 500


def test_backfill_completed_skips_missing_jsonl(tmp_path: Path, capsys) -> None:
    mem = tmp_path / "memory"
    mem.mkdir()
    _seed_session(mem, "gone", "/does/not/exist.jsonl")

    rc = cli._run_backfill_completed(mem, "2026-07-09", apply=True)
    assert rc == 0  # missing jsonl is skipped, not a crash
    out = capsys.readouterr().out
    assert "skipped" in out.lower()
    assert _completed_offset(mem, "gone") is None


def test_memory_cli_routes_backfill_completed(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake(root: Path, date_str: str, *, apply: bool) -> int:
        seen["date"] = date_str
        seen["root"] = str(root)
        seen["apply"] = apply
        return 0

    monkeypatch.setattr(cli, "_run_backfill_completed", fake)
    rc = cli._run_memory_cli(
        ["backfill-completed", "--date", "2026-07-09", "--root", str(tmp_path)]
    )
    assert rc == 0
    assert seen == {"date": "2026-07-09", "root": str(tmp_path), "apply": False}


def test_backfill_completed_sets_extracted_for_already_extracted(
    tmp_path: Path, capsys
) -> None:
    """A session 040-a already extracted (extracted_at set) gets extr=comp.

    Otherwise find_incremental sees comp>0>extr(NULL=0) and re-distills the
    whole session, duplicating every episode segment (the 07-09 regression).
    """
    from trowel_py.memory.sessions_repo import (
        SessionRecord,
        create_sessions_repository,
        open_sessions_db,
    )

    mem = tmp_path / "memory"
    mem.mkdir()
    jsonl = tmp_path / "s.jsonl"
    jsonl.write_text("x" * 500)
    conn = open_sessions_db(mem)
    create_sessions_repository(conn).register(
        SessionRecord(
            cc_session_id="s1",
            workdir="/proj",
            date="2026-07-09",
            jsonl_path=str(jsonl),
            registered_at="t",
            extracted_at="2026-07-10T08:00:00",  # 040-a already extracted
        )
    )
    conn.close()

    rc = cli._run_backfill_completed(mem, "2026-07-09", apply=True)
    assert rc == 0

    conn2 = open_sessions_db(mem)
    rec = create_sessions_repository(conn2).find_by_date("2026-07-09")[0]
    conn2.close()
    assert rec.last_completed_offset == 500
    assert rec.last_extracted_offset == 500  # extr = comp (already extracted)
    assert rec.last_extracted_at == "2026-07-10T08:00:00"  # preserved


# ---------- slice-063: `trowel-py memory tidy --status / --catchup` ----------


def test_memory_tidy_status_prints_watermark_and_pending(
    tmp_path: Path, capsys
) -> None:
    import json

    from trowel_py.memory.tidy_state import TidyState, save_state

    save_state(tmp_path, TidyState(weekly_last="2026-W29", monthly_last="2026-06"))
    rc = cli._run_memory_cli(["tidy", "--status", "--root", str(tmp_path)])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["weekly"]["last_successful"] == "2026-W29"
    assert data["monthly"]["last_successful"] == "2026-06"
    assert isinstance(data["weekly"]["pending"], list)


def test_memory_tidy_status_empty_state_bootstrap(
    tmp_path: Path, capsys
) -> None:
    """no watermark yet → status still works; pending shows the bootstrap
    single most-recent period (C-8), never the whole history."""
    import json

    rc = cli._run_memory_cli(["tidy", "--status", "--root", str(tmp_path)])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["weekly"]["last_successful"] is None
    assert len(data["weekly"]["pending"]) == 1


def test_memory_tidy_catchup_requires_from_and_scope(
    tmp_path: Path, capsys
) -> None:
    rc = cli._run_memory_cli(["tidy", "--catchup", "--root", str(tmp_path)])
    assert rc == 2
    assert "--from" in capsys.readouterr().out


def test_memory_tidy_catchup_runs_explicit_range(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """--catchup --from PERIOD --scope weekly routes to run_explicit_catchup
    with the resolved root + a lazy provider factory (factory is never called
    because the catchup fn is monkeypatched, so no real provider is built)."""
    import json

    import trowel_py.memory.tidy_scheduler as tsmod

    seen: dict[str, object] = {}

    def fake(root, scope, from_period, provider_factory, *, now=None):
        seen["root"] = str(root)
        seen["scope"] = scope
        seen["from"] = from_period
        assert callable(provider_factory)  # built lazily, not here
        return {
            "scope": scope,
            "from": from_period,
            "planned": [from_period],
            "ran": [from_period],
            "failed_at": None,
            "watermark": from_period,
        }

    monkeypatch.setattr(tsmod, "run_explicit_catchup", fake)
    rc = cli._run_memory_cli(
        [
            "tidy", "--catchup", "--from", "2026-W20", "--scope", "weekly",
            "--root", str(tmp_path),
        ]
    )
    assert rc == 0
    assert seen == {"root": str(tmp_path), "scope": "weekly", "from": "2026-W20"}
    out = json.loads(capsys.readouterr().out)
    assert out["from"] == "2026-W20"


