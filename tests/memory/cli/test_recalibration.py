from __future__ import annotations

import json
from pathlib import Path

from trowel_py import cli

from .support import seed_user_session


def test_profile_recalibrate_default_is_plan(tmp_path: Path, capsys) -> None:
    memory_root = tmp_path / "memory"
    memory_root.mkdir()
    jsonl = tmp_path / "session.jsonl"
    jsonl.write_text("payload", encoding="utf-8")
    seed_user_session(memory_root, "s1", str(jsonl))

    rc = cli._run_memory_cli(
        ["profile-recalibrate", "--all", "--root", str(memory_root)]
    )

    assert rc == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["scope"] == {"all": True, "from": None}
    assert [session["cc_session_id"] for session in plan["sessions"]] == ["s1"]
    assert plan["sessions"][0]["end_offset"] == 500
    assert plan["estimated_agent_calls"] == 1
    assert not (memory_root / "meta" / "profile-recalibration").exists()


def test_profile_recalibrate_plan_from_date(tmp_path: Path, capsys) -> None:
    memory_root = tmp_path / "memory"
    memory_root.mkdir()
    jsonl = tmp_path / "session.jsonl"
    jsonl.write_text("payload", encoding="utf-8")
    seed_user_session(memory_root, "s1", str(jsonl))

    rc = cli._run_memory_cli(
        [
            "profile-recalibrate",
            "--from",
            "2026-07-01",
            "--root",
            str(memory_root),
        ]
    )

    assert rc == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["scope"] == {"all": False, "from": "2026-07-01"}


def test_profile_recalibrate_requires_scope(tmp_path: Path, capsys) -> None:
    memory_root = tmp_path / "memory"
    memory_root.mkdir()

    rc = cli._run_memory_cli(["profile-recalibrate", "--root", str(memory_root)])

    assert rc == 2
    output = capsys.readouterr().out
    assert "--all" in output or "scope" in output


def test_profile_recalibrate_rejects_both_scope_modes(
    tmp_path: Path,
    capsys,
) -> None:
    memory_root = tmp_path / "memory"
    memory_root.mkdir()

    rc = cli._run_memory_cli(
        [
            "profile-recalibrate",
            "--all",
            "--from",
            "2026-07-01",
            "--root",
            str(memory_root),
        ]
    )

    assert rc == 2


def test_profile_recalibrate_run_requires_proxy(tmp_path: Path, capsys) -> None:
    memory_root = tmp_path / "memory"
    memory_root.mkdir()

    rc = cli._run_memory_cli(
        [
            "profile-recalibrate",
            "--all",
            "--run",
            "--root",
            str(memory_root),
        ]
    )

    assert rc == 2
    assert "proxy" in capsys.readouterr().out.lower()


def test_profile_recalibrate_run_with_proxy_dispatches(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    import trowel_py.memory.profile_recalibrate as recalibration_module

    seen: dict[str, object] = {}

    class FakeResult:
        def to_report_dict(self) -> dict[str, str]:
            return {"status": "complete", "run_id": "fake"}

    async def fake_run(root, *, scope_all, from_date, proxy_base_url, **kwargs):
        seen["root"] = str(root)
        seen["scope_all"] = scope_all
        seen["from_date"] = from_date
        seen["proxy_base_url"] = proxy_base_url
        seen["settings_path"] = kwargs.get("settings_path")
        return FakeResult()

    monkeypatch.setattr(recalibration_module, "run_recalibration", fake_run)
    memory_root = tmp_path / "memory"
    memory_root.mkdir()

    rc = cli._run_memory_cli(
        [
            "profile-recalibrate",
            "--all",
            "--run",
            "--proxy-base-url",
            "http://127.0.0.1:8000",
            "--root",
            str(memory_root),
        ]
    )

    assert rc == 0
    assert seen["scope_all"] is True
    assert seen["proxy_base_url"] == "http://127.0.0.1:8000"
    assert seen["root"] == str(memory_root)
    settings_path = Path(str(seen["settings_path"]))
    assert settings_path.name == "settings.json"
    assert settings_path.parent.name == ".claude"
    assert json.loads(capsys.readouterr().out)["status"] == "complete"
