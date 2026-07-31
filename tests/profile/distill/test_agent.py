from __future__ import annotations

import json
from pathlib import Path

import pytest

import trowel_py.profile.distill.processor as distill_processor
from tests.profile.distill.support import (
    ERROR,
    FINISHED,
    FakeHost,
    VALID_DRAFT,
    fake_host_factory,
    seed_session,
    session_record,
)
from trowel_py.profile.distill import DistillError, drive_and_gate, run_claude_session
from trowel_py.profile.suggestions import PROFILE_DISTILL_POLICY_VERSION


async def test_run_claude_session_parses_draft(tmp_path: Path) -> None:
    suggestions = await run_claude_session(
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


async def test_run_claude_session_stamps_cc_session_in_sources(tmp_path: Path) -> None:
    suggestions = await run_claude_session(
        session_record(sid="cc-sess-xyz"),
        "2026-07-14",
        tmp_path / "memory",
        proxy_base_url="http://x",
        host_factory=fake_host_factory([FINISHED], VALID_DRAFT),
    )
    assert "cc-sess-xyz" in suggestions[0].sources
    assert "用户提到缓存失效排查" in suggestions[0].sources


async def test_run_claude_session_empty_draft_ok(tmp_path: Path) -> None:
    empty = json.dumps({"suggestions": []})
    suggestions = await run_claude_session(
        session_record(),
        "2026-07-14",
        tmp_path / "memory",
        proxy_base_url="http://x",
        host_factory=fake_host_factory([FINISHED], empty),
    )
    assert suggestions == []


async def test_run_claude_session_no_draft_raises(tmp_path: Path) -> None:
    with pytest.raises(DistillError):
        await run_claude_session(
            session_record(),
            "2026-07-14",
            tmp_path / "memory",
            proxy_base_url="http://x",
            host_factory=fake_host_factory([FINISHED], draft_text=None),
        )


async def test_run_claude_session_not_finished_raises(tmp_path: Path) -> None:
    with pytest.raises(DistillError):
        await run_claude_session(
            session_record(),
            "2026-07-14",
            tmp_path / "memory",
            proxy_base_url="http://x",
            host_factory=fake_host_factory([ERROR], VALID_DRAFT),
        )


async def test_run_claude_session_bad_dimension_raises(tmp_path: Path) -> None:
    bad = json.dumps({"suggestions": [{"dimension": "personality", "body": "x"}]})
    with pytest.raises(DistillError):
        await run_claude_session(
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
    await run_claude_session(
        session_record(),
        "2026-07-14",
        tmp_path / "memory",
        proxy_base_url="http://127.0.0.1:8000",
        settings_path="/home/u/.claude/settings.json",
    )
    assert captured["proxy_base_url"] == "http://127.0.0.1:8000"
    assert captured["session_kind"] == "distill"
    assert captured["settings_path"] == "/home/u/.claude/settings.json"


async def test_run_claude_session_feeds_only_current_policy_queue_to_dedup(
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
    real_build = distill_processor.build_source_distill_prompt

    def spy(source, existing, profile):
        captured["pvs"] = [suggestion.policy_version for suggestion in existing]
        return real_build(source, existing, profile)

    monkeypatch.setattr(distill_processor, "build_source_distill_prompt", spy)
    await run_claude_session(
        session_record(),
        "2026-07-17",
        root,
        proxy_base_url="http://x",
        host_factory=fake_host_factory([FINISHED], VALID_DRAFT),
    )
    assert captured["pvs"] == [PROFILE_DISTILL_POLICY_VERSION]


async def test_drive_and_gate_uses_source_id_without_runtime_session(
    tmp_path: Path,
) -> None:
    """共享执行器只接收来源身份，不要求来源是 Claude SessionRecord。"""
    captured: list[str] = []
    workdir = tmp_path / "work"
    workdir.mkdir()

    def factory(source_id: str, host_workdir: Path):
        captured.append(source_id)
        (host_workdir / "suggestions-draft.json").write_text(
            VALID_DRAFT,
            encoding="utf-8",
        )
        return FakeHost([FINISHED])

    gated = await drive_and_gate(
        "codex:thread-1:turn-2",
        workdir,
        "prompt",
        proxy_base_url="http://x",
        settings_path=None,
        host_factory=factory,
        date_str="2026-07-31",
    )

    assert captured == ["codex:thread-1:turn-2"]
    assert gated.accepted[0].sources[0] == "codex:thread-1:turn-2"
