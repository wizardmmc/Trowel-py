from __future__ import annotations

import json
from pathlib import Path

import pytest

import trowel_py.memory.profile_distill.agent as agent_module
from tests.memory.profile_distill.support import (
    ERROR,
    FINISHED,
    VALID_DRAFT,
    fake_host_factory,
    seed_session,
    session_record,
)
from trowel_py.memory.profile_distill_job import DistillError, run_one_session
from trowel_py.memory.profile_suggestions import PROFILE_DISTILL_POLICY_VERSION


async def test_run_one_session_parses_draft(tmp_path: Path) -> None:
    suggestions = await run_one_session(
        session_record(),
        "2026-07-14",
        tmp_path / "memory",
        proxy_base_url="http://127.0.0.1:8000",
        host_factory=fake_host_factory([FINISHED], VALID_DRAFT),
    )
    assert len(suggestions) == 1
    suggestion = suggestions[0]
    assert suggestion.dimension == "ability"
    assert suggestion.body == "熟悉缓存一致性 / 并发调试"
    assert suggestion.status == "pending"
    assert suggestion.date == "2026-07-14"
    assert suggestion.id


async def test_run_one_session_stamps_cc_session_in_sources(tmp_path: Path) -> None:
    suggestions = await run_one_session(
        session_record(sid="cc-sess-xyz"),
        "2026-07-14",
        tmp_path / "memory",
        proxy_base_url="http://x",
        host_factory=fake_host_factory([FINISHED], VALID_DRAFT),
    )
    assert "cc-sess-xyz" in suggestions[0].sources
    assert "用户提到缓存失效排查" in suggestions[0].sources


async def test_run_one_session_empty_draft_ok(tmp_path: Path) -> None:
    empty = json.dumps({"suggestions": []})
    suggestions = await run_one_session(
        session_record(),
        "2026-07-14",
        tmp_path / "memory",
        proxy_base_url="http://x",
        host_factory=fake_host_factory([FINISHED], empty),
    )
    assert suggestions == []


async def test_run_one_session_no_draft_raises(tmp_path: Path) -> None:
    with pytest.raises(DistillError):
        await run_one_session(
            session_record(),
            "2026-07-14",
            tmp_path / "memory",
            proxy_base_url="http://x",
            host_factory=fake_host_factory([FINISHED], draft_text=None),
        )


async def test_run_one_session_not_finished_raises(tmp_path: Path) -> None:
    with pytest.raises(DistillError):
        await run_one_session(
            session_record(),
            "2026-07-14",
            tmp_path / "memory",
            proxy_base_url="http://x",
            host_factory=fake_host_factory([ERROR], VALID_DRAFT),
        )


async def test_run_one_session_bad_dimension_raises(tmp_path: Path) -> None:
    bad = json.dumps({"suggestions": [{"dimension": "personality", "body": "x"}]})
    with pytest.raises(DistillError):
        await run_one_session(
            session_record(),
            "2026-07-14",
            tmp_path / "memory",
            proxy_base_url="http://x",
            host_factory=fake_host_factory([FINISHED], bad),
        )


async def test_cchost_built_with_proxy_distill_kind_and_settings_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """真实 host 必须经代理、隔离 session kind，并保留 provider settings。"""
    captured: dict = {}

    class FakeCCHost:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self._workdir = str(kwargs.get("workdir"))

        async def send(self, prompt: str):
            Path(self._workdir, "suggestions-draft.json").write_text(
                VALID_DRAFT, encoding="utf-8"
            )
            yield FINISHED

        async def close(self) -> None:
            pass

    monkeypatch.setattr("trowel_py.cc_host.service.CCHost", FakeCCHost)
    await run_one_session(
        session_record(),
        "2026-07-14",
        tmp_path / "memory",
        proxy_base_url="http://127.0.0.1:8000",
        settings_path="/home/u/.claude/settings.json",
    )
    assert captured["proxy_base_url"] == "http://127.0.0.1:8000"
    assert captured["session_kind"] == "distill"
    assert captured["settings_path"] == "/home/u/.claude/settings.json"


async def test_run_one_session_feeds_only_current_policy_queue_to_dedup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "memory"
    root.mkdir(parents=True, exist_ok=True)
    seed_session(root, "s1", completed=1000)
    (root / "meta").mkdir(exist_ok=True)
    (root / "meta" / "profile-suggestions.json").write_text(
        json.dumps(
            {
                "suggestions": [
                    {
                        "id": "v1",
                        "dimension": "ability",
                        "body": "v1 long ability body",
                        "sources": ["old"],
                        "date": "2026-07-01",
                        "status": "pending",
                    },
                    {
                        "id": "v2",
                        "dimension": "goal",
                        "body": "v2 short goal",
                        "sources": ["new"],
                        "date": "2026-07-15",
                        "status": "pending",
                        "policy_version": PROFILE_DISTILL_POLICY_VERSION,
                    },
                ],
                "updated": "2026-07-15",
            }
        ),
        encoding="utf-8",
    )

    captured: dict[str, list] = {}
    real_build = agent_module.build_distill_prompt

    def spy(jsonl_path: str, existing, profile, **kwargs):
        captured["pvs"] = [suggestion.policy_version for suggestion in existing]
        return real_build(jsonl_path, existing, profile, **kwargs)

    monkeypatch.setattr(agent_module, "build_distill_prompt", spy)
    await run_one_session(
        session_record(),
        "2026-07-17",
        root,
        proxy_base_url="http://x",
        host_factory=fake_host_factory([FINISHED], VALID_DRAFT),
    )
    assert captured["pvs"] == [PROFILE_DISTILL_POLICY_VERSION]
