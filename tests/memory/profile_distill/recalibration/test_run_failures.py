import json
from pathlib import Path

import pytest

from trowel_py.memory.profile_recalibrate import (
    RecalibrationScopeError,
    run_recalibration,
)
from trowel_py.memory.sessions_repo import SessionRecord

from .support import (
    FINISHED,
    VALID_DRAFT,
    FakeHost,
    seed_live_files,
    seed_session,
    sha,
)


async def test_run_requires_explicit_scope(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    with pytest.raises(RecalibrationScopeError):
        await run_recalibration(
            root, scope_all=False, from_date=None, proxy_base_url="http://x"
        )


async def test_run_requires_proxy(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    with pytest.raises(ValueError):
        await run_recalibration(
            root, scope_all=True, from_date=None, proxy_base_url=""
        )


async def test_run_marks_incomplete_on_session_failure(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    seed_live_files(root)
    jsonl1 = tmp_path / "s1.jsonl"
    jsonl1.write_text("payload", encoding="utf-8")
    jsonl2 = tmp_path / "s2.jsonl"
    jsonl2.write_text("payload", encoding="utf-8")
    seed_session(root, "s1", completed=500, jsonl_path=str(jsonl1))
    seed_session(
        root,
        "s2",
        completed=500,
        jsonl_path=str(jsonl2),
        registered_at="2026-07-15T10:00:00",
    )

    def factory(session: SessionRecord, workdir: Path) -> FakeHost:
        if session.cc_session_id == "s2":
            (workdir / "suggestions-draft.json").write_text(
                VALID_DRAFT, encoding="utf-8"
            )
        return FakeHost([FINISHED])

    result = await run_recalibration(
        root,
        scope_all=True,
        from_date=None,
        proxy_base_url="http://x",
        host_factory=factory,
        run_id="run-2",
        created_at="2026-07-17T02:00:00",
    )
    assert result.status == "incomplete"
    assert "s1" in result.failed_session_ids
    assert result.sessions_failed == 1
    assert result.sessions_ok == 1
    assert result.accepted_count == 1


class RaisingHost:
    async def send(self, prompt: str):
        raise RuntimeError("proxy exploded")
        yield

    async def close(self) -> None:
        pass


async def test_run_marks_incomplete_on_unexpected_exception(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    seed_live_files(root)
    jsonl = tmp_path / "s1.jsonl"
    jsonl.write_text("payload", encoding="utf-8")
    seed_session(root, "s1", completed=500, jsonl_path=str(jsonl))

    def factory(session: SessionRecord, workdir: Path) -> RaisingHost:
        return RaisingHost()

    result = await run_recalibration(
        root,
        scope_all=True,
        from_date=None,
        proxy_base_url="http://x",
        host_factory=factory,
        run_id="run-err",
        created_at="2026-07-17T02:00:00",
    )
    assert result.status == "incomplete"
    assert "s1" in result.failed_session_ids
    staging = root / "meta" / "profile-recalibration" / "run-err"
    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "incomplete"
    assert (staging / "report.json").exists()


async def test_run_marks_incomplete_when_live_changes_under_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    seed_live_files(root)
    jsonl = tmp_path / "s1.jsonl"
    jsonl.write_text("payload", encoding="utf-8")
    seed_session(root, "s1", completed=500, jsonl_path=str(jsonl))

    def factory(session: SessionRecord, workdir: Path) -> FakeHost:
        (workdir / "suggestions-draft.json").write_text(
            VALID_DRAFT, encoding="utf-8"
        )
        (root / "profile.md").write_text(
            "---\nupdated: 2026-07-17\nsource: user-edit\n---\n## 能力水平\n改了\n",
            encoding="utf-8",
        )
        return FakeHost([FINISHED])

    result = await run_recalibration(
        root,
        scope_all=True,
        from_date=None,
        proxy_base_url="http://x",
        host_factory=factory,
        run_id="run-chg",
        created_at="2026-07-17T02:00:00",
    )
    assert result.status == "incomplete"
    staging = root / "meta" / "profile-recalibration" / "run-chg"
    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["live_changed_during_run"] is True


async def test_run_real_cchost_gets_null_registrar(
    tmp_path: Path, monkeypatch
) -> None:
    import trowel_py.cc_host.service as service_module

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

    monkeypatch.setattr(service_module, "CCHost", FakeCCHost)

    root = tmp_path / "memory"
    root.mkdir()
    seed_live_files(root)
    jsonl = tmp_path / "s1.jsonl"
    jsonl.write_text("payload", encoding="utf-8")
    seed_session(root, "s1", completed=500, jsonl_path=str(jsonl))

    before_db = sha(root / "meta" / "sessions.db")
    await run_recalibration(
        root,
        scope_all=True,
        from_date=None,
        proxy_base_url="http://127.0.0.1:8000",
        settings_path="/home/u/.claude/settings.json",
        run_id="run-null",
        created_at="2026-07-17T02:00:00",
    )
    registrar = captured.get("session_registrar")
    assert registrar is not None
    assert captured["proxy_base_url"] == "http://127.0.0.1:8000"
    assert captured["session_kind"] == "distill"
    registrar.register(SessionRecord(cc_session_id="x", workdir="", date=""))
    registrar.update_completed("x", 999)
    assert sha(root / "meta" / "sessions.db") == before_db
