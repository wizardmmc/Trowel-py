from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.memory.compress import compress_weekly
from trowel_py.memory.regeneration import (
    apply_regeneration,
    plan_regeneration,
    run_regeneration,
)

from tests.memory.compress.support import FakeProvider, items_json

from .support import seed_day, successful_provider, weekly_json


def _missing_plan(root: Path):
    return plan_regeneration(
        root,
        layer="daily",
        from_period="2026-07-06",
        to_period="2026-07-06",
        mode="missing",
    )


def test_run_writes_only_staging_and_is_idempotent(tmp_path: Path) -> None:
    seed_day(tmp_path)
    plan = _missing_plan(tmp_path)
    provider = successful_provider()

    run = run_regeneration(tmp_path, plan.plan_id, provider=provider)

    assert run.status == "completed"
    assert [result.status for result in run.results] == [
        "success",
        "success",
        "success",
    ]
    staging = Path(run.staging_root)
    assert (staging / "diary" / "daily" / "2026-07-06.md").exists()
    assert (staging / "diary" / "weekly" / "2026-W28.md").exists()
    assert (staging / "diary" / "monthly" / "2026-07.md").exists()
    assert not (tmp_path / "diary" / "daily" / "2026-07-06.md").exists()
    first_calls = len(provider.calls)

    repeated = run_regeneration(tmp_path, plan.plan_id, provider=provider)

    assert repeated.run_id == run.run_id
    assert len(provider.calls) == first_calls


def test_run_recovers_an_incomplete_staging_directory(tmp_path: Path) -> None:
    seed_day(tmp_path)
    plan = _missing_plan(tmp_path)
    incomplete = (
        tmp_path
        / "meta"
        / "regeneration"
        / "runs"
        / f"run-{plan.plan_id}"
        / "staging"
    )
    incomplete.mkdir(parents=True)

    run = run_regeneration(tmp_path, plan.plan_id, provider=successful_provider())

    assert run.status == "completed"
    assert (Path(run.staging_root) / ".ready").exists()


def test_run_rejects_direct_live_target(tmp_path: Path) -> None:
    seed_day(tmp_path)
    plan = _missing_plan(tmp_path)

    with pytest.raises(ValueError, match="only supports target='staging'"):
        run_regeneration(
            tmp_path,
            plan.plan_id,
            target="live",
            provider=successful_provider(),
        )

    assert not (tmp_path / "diary" / "daily" / "2026-07-06.md").exists()


def test_failed_run_stops_dependents_and_can_resume(tmp_path: Path) -> None:
    seed_day(tmp_path)
    plan = _missing_plan(tmp_path)
    failing = FakeProvider(
        responses=[
            items_json(("outcome", "完成 A", "S1")),
            "bad weekly",
            "still bad weekly",
        ]
    )

    first = run_regeneration(tmp_path, plan.plan_id, provider=failing)

    assert [result.status for result in first.results] == [
        "success",
        "failed",
        "skipped",
    ]
    with pytest.raises(ValueError, match="not completed"):
        apply_regeneration(tmp_path, first.run_id)

    resumed = run_regeneration(
        tmp_path,
        plan.plan_id,
        provider=FakeProvider(responses=[weekly_json(), "本月完成 A。"]),
    )

    assert resumed.run_id == first.run_id
    assert resumed.status == "completed"
    assert all(result.status == "success" for result in resumed.results)


def test_apply_is_explicit_and_does_not_touch_unrelated_memory(
    tmp_path: Path,
) -> None:
    seed_day(tmp_path)
    note = tmp_path / "notes" / "keep.md"
    note.parent.mkdir()
    note.write_text("KEEP-NOTE", encoding="utf-8")
    dictionary = tmp_path / "dictionary-L0.md"
    dictionary.write_text("KEEP-DICTIONARY", encoding="utf-8")
    watermark = tmp_path / "meta" / "review-watermark.json"
    watermark.parent.mkdir(parents=True)
    watermark.write_text("KEEP-WATERMARK", encoding="utf-8")
    run = run_regeneration(
        tmp_path,
        _missing_plan(tmp_path).plan_id,
        provider=successful_provider(),
    )

    report = apply_regeneration(tmp_path, run.run_id)

    assert report["applied"] is True
    assert report["layers"] == ["daily", "weekly", "monthly"]
    assert (tmp_path / "diary" / "daily" / "2026-07-06.md").exists()
    assert (tmp_path / "diary" / "weekly" / "2026-W28.md").exists()
    assert (tmp_path / "diary" / "monthly" / "2026-07.md").exists()
    assert note.read_text(encoding="utf-8") == "KEEP-NOTE"
    assert dictionary.read_text(encoding="utf-8") == "KEEP-DICTIONARY"
    assert watermark.read_text(encoding="utf-8") == "KEEP-WATERMARK"


def test_apply_rolls_back_every_layer_when_publish_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import trowel_py.memory.regeneration.service as service

    seed_day(tmp_path)
    run = run_regeneration(
        tmp_path,
        _missing_plan(tmp_path).plan_id,
        provider=successful_provider(),
    )
    old_paths = {
        "daily": tmp_path / "diary" / "daily" / "2026-07-06.md",
        "weekly": tmp_path / "diary" / "weekly" / "2026-W28.md",
        "monthly": tmp_path / "diary" / "monthly" / "2026-07.md",
    }
    for layer, path in old_paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"OLD-{layer}", encoding="utf-8")
    real_publish = service._publish_artifact

    def fail_weekly(path: Path, content: bytes | None) -> None:
        if path == old_paths["weekly"]:
            raise OSError("injected weekly publish failure")
        real_publish(path, content)

    monkeypatch.setattr(service, "_publish_artifact", fail_weekly)

    with pytest.raises(OSError, match="injected"):
        apply_regeneration(tmp_path, run.run_id)

    for layer, path in old_paths.items():
        assert path.read_text(encoding="utf-8") == f"OLD-{layer}"


def test_apply_rejects_artifact_path_outside_memory_root(tmp_path: Path) -> None:
    import json

    seed_day(tmp_path)
    run = run_regeneration(
        tmp_path,
        _missing_plan(tmp_path).plan_id,
        provider=successful_provider(),
    )
    manifest = (
        tmp_path
        / "meta"
        / "regeneration"
        / "runs"
        / run.run_id
        / "manifest.json"
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["results"][0]["artifacts"][0]["relative_path"] = "../../escape.md"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe staged artifact"):
        apply_regeneration(tmp_path, run.run_id)

    assert not (tmp_path.parent / "escape.md").exists()


def test_weekly_regeneration_does_not_call_tidy_or_dictionary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import trowel_py.memory.dictionary as dictionary
    import trowel_py.memory.tidy as tidy

    seed_day(tmp_path)
    from .support import generate_day

    generate_day(tmp_path)
    compress_weekly(tmp_path, "2026-W28", FakeProvider(weekly_json()))
    monkeypatch.setattr(
        tidy,
        "run_weekly_tidy",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("tidy called")),
    )
    monkeypatch.setattr(
        dictionary,
        "rebuild_dictionary",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("dictionary called")
        ),
    )
    plan = plan_regeneration(
        tmp_path,
        layer="weekly",
        from_period="2026-W28",
        to_period="2026-W28",
        mode="all",
    )

    run = run_regeneration(
        tmp_path,
        plan.plan_id,
        provider=FakeProvider(responses=[weekly_json(), "本月完成 A。"]),
    )

    assert run.status == "completed"
