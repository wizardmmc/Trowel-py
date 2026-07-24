import json
from pathlib import Path

from trowel_py.memory.profile_recalibrate import run_recalibration

from .support import (
    VALID_DRAFT,
    host_factory,
    live_hashes,
    seed_live_files,
    seed_session,
    sha,
)


async def test_run_produces_staging_and_report(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    seed_live_files(root)
    jsonl = tmp_path / "s1.jsonl"
    jsonl.write_text("payload", encoding="utf-8")
    seed_session(root, "s1", completed=500, jsonl_path=str(jsonl))

    result = await run_recalibration(
        root,
        scope_all=True,
        from_date=None,
        proxy_base_url="http://x",
        host_factory=host_factory(VALID_DRAFT),
        run_id="run-1",
        created_at="2026-07-17T02:00:00",
    )
    assert result.status == "complete"
    assert result.sessions_ok == 1
    assert result.sessions_failed == 0
    assert result.accepted_count == 1
    assert result.policy_version == 2
    assert result.by_dimension == {"ability": 1}

    staging = root / "meta" / "profile-recalibration" / "run-1"
    assert (staging / "manifest.json").exists()
    assert (staging / "staged-suggestions.json").exists()
    assert (staging / "report.json").exists()
    assert (staging / "baseline" / "profile.md").exists()

    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == "run-1"
    assert manifest["policy_version"] == 2
    assert manifest["status"] == "complete"
    assert manifest["scope"] == {"all": True, "from": None}
    assert manifest["source_hashes"]["profile"] != "missing"

    staged = json.loads((staging / "staged-suggestions.json").read_text(encoding="utf-8"))
    assert len(staged["suggestions"]) == 1
    assert staged["suggestions"][0]["policy_version"] == 2
    assert staged["suggestions"][0]["body"] == "熟悉 Python / 能编写自动化测试"

    report = json.loads((staging / "report.json").read_text(encoding="utf-8"))
    assert report["raw_count"] == 1
    assert report["accepted_count"] == 1
    assert report["body_max_chars"] == len("熟悉 Python / 能编写自动化测试")


async def test_run_leaves_live_byte_identical(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    seed_live_files(root)
    jsonl = tmp_path / "s1.jsonl"
    jsonl.write_text("payload", encoding="utf-8")
    seed_session(root, "s1", completed=500, jsonl_path=str(jsonl))

    before = live_hashes(root, [jsonl])
    await run_recalibration(
        root,
        scope_all=True,
        from_date=None,
        proxy_base_url="http://x",
        host_factory=host_factory(VALID_DRAFT),
        run_id="run-3",
        created_at="2026-07-17T02:00:00",
    )
    assert live_hashes(root, [jsonl]) == before


async def test_run_baseline_restores_live_files(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    seed_live_files(root)
    jsonl = tmp_path / "s1.jsonl"
    jsonl.write_text("payload", encoding="utf-8")
    seed_session(root, "s1", completed=500, jsonl_path=str(jsonl))

    await run_recalibration(
        root,
        scope_all=True,
        from_date=None,
        proxy_base_url="http://x",
        host_factory=host_factory(VALID_DRAFT),
        run_id="run-4",
        created_at="2026-07-17T02:00:00",
    )
    baseline = root / "meta" / "profile-recalibration" / "run-4" / "baseline"
    assert sha(baseline / "profile.md") == sha(root / "profile.md")
    assert sha(baseline / "profile-suggestions.json") == sha(
        root / "meta" / "profile-suggestions.json"
    )
    assert sha(baseline / "profile-distill-state.json") == sha(
        root / "meta" / "profile-distill-state.json"
    )


async def test_run_manifest_records_missing_baseline(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    (root / "profile.md").write_text("only profile", encoding="utf-8")
    jsonl = tmp_path / "s1.jsonl"
    jsonl.write_text("payload", encoding="utf-8")
    seed_session(root, "s1", completed=500, jsonl_path=str(jsonl))

    await run_recalibration(
        root,
        scope_all=True,
        from_date=None,
        proxy_base_url="http://x",
        host_factory=host_factory(VALID_DRAFT),
        run_id="run-5",
        created_at="2026-07-17T02:00:00",
    )
    staging = root / "meta" / "profile-recalibration" / "run-5"
    baseline = staging / "baseline"
    assert not (baseline / "profile-suggestions.json").exists()
    assert not (baseline / "profile-distill-state.json").exists()
    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_hashes"]["suggestions"] == "missing"
    assert manifest["source_hashes"]["watermark"] == "missing"
    assert manifest["source_hashes"]["profile"] != "missing"


async def test_run_dedups_against_staging_only_not_live_queue(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    (root / "meta").mkdir(parents=True)
    (root / "meta" / "profile-suggestions.json").write_text(
        json.dumps(
            {
                "suggestions": [
                    {
                        "id": "v1-old",
                        "dimension": "methodology",
                        "body": "一条长 v1 methodology 描述带例子和评价",
                        "sources": ["old"],
                        "date": "2026-07-01",
                        "status": "pending",
                    }
                ],
                "updated": "2026-07-01",
            }
        ),
        encoding="utf-8",
    )
    jsonl = tmp_path / "s1.jsonl"
    jsonl.write_text("payload", encoding="utf-8")
    seed_session(root, "s1", completed=500, jsonl_path=str(jsonl))

    draft = json.dumps(
        {
            "suggestions": [
                {
                    "dimension": "methodology",
                    "body": "commit 要让外行看懂",
                    "sources": ["用户原话"],
                    "rationale": "明确表述为通用原则",
                }
            ]
        }
    )
    result = await run_recalibration(
        root,
        scope_all=True,
        from_date=None,
        proxy_base_url="http://x",
        host_factory=host_factory(draft),
        run_id="run-6",
        created_at="2026-07-17T02:00:00",
    )
    assert result.accepted_count == 1
    assert result.staged_suggestions[0].body == "commit 要让外行看懂"
    live = json.loads(
        (root / "meta" / "profile-suggestions.json").read_text(encoding="utf-8")
    )
    assert [suggestion["id"] for suggestion in live["suggestions"]] == ["v1-old"]
