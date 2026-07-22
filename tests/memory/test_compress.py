"""slice-062 daily compression + slice-041 weekly/monthly tests.

Daily (slice-062 rewrite): the LLM no longer emits the final Markdown. It emits
typed items (each citing a source segment id); Python validates, dedupes,
budget-selects and renders the fixed 进展/更正/待续 Markdown. Failures write a
``generation_status=fallback`` notice (never the full aggregate). Weekly/monthly
(slice-041) are unchanged: each caps at ~800 chars, bypass files are week-level
only (S3 — never enter monthly).
"""
from __future__ import annotations

import json
from pathlib import Path

from trowel_py.memory.compress import (
    _parse_weekly_output,
    compress_daily,
    compress_monthly,
    compress_weekly,
    daily_dates_needing_rebuild,
    write_fallback_daily,
)
from trowel_py.memory.draft import DraftDiary
from trowel_py.memory.store import MemoryStore, _dump_frontmatter, _split_frontmatter
from trowel_py.memory.types import PersistContext


class FakeProvider:
    """Minimal LLMProvider: returns a canned response, records calls.

    ``responses`` (when given) are consumed in call order — used to simulate a
    first invalid response then a valid one (the slice-062 retry). ``raise_on``
    makes the call raise (provider failure path).
    """

    def __init__(
        self,
        response: str = "压缩版",
        *,
        responses: list[str] | None = None,
        raise_on: Exception | None = None,
    ) -> None:
        self.response = response
        self._seq = list(responses) if responses else None
        self.raise_on = raise_on
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        if self.raise_on is not None:
            raise self.raise_on
        if self._seq is not None:
            return self._seq.pop(0)
        return self.response


def _episode(root: Path, sid: str, date: str, body: str,
             registered_at: str = "2026-07-01T10:00:00") -> Path:
    path = root / "episodes" / f"{sid}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = {
        "type": "episode", "cc_session_id": sid, "workdir": "/tmp",
        "registered_at": registered_at, "review_date": date,
        "activity_dates": [date], "source_jsonl": "/tmp/x.jsonl", "segments": [],
    }
    path.write_text(_dump_frontmatter(fm, body), encoding="utf-8")
    return path


def _structured_episode(
    root: Path,
    sid: str,
    date: str = "2026-07-01",
    *,
    outcomes: tuple[str, ...] = (),
    decisions: tuple[str, ...] = (),
    corrections: tuple[str, ...] = (),
    open_loops: tuple[str, ...] = (),
    events: str = "",
    segment_id: str | None = None,
    registered_at: str = "2026-07-01T10:00:00",
) -> None:
    """Write one structured episode segment (slice-062 experience track)."""
    ctx = PersistContext(
        segment_id=segment_id or f"{sid}:0:end",
        cc_session_id=sid,
        workdir="/tmp",
        registered_at=registered_at,
        review_date=date,
        source_jsonl="/tmp/x.jsonl",
        activity_dates=(date,),
        date_basis="jsonl_timestamp",
        processed_date=date,
    )
    MemoryStore(root).write_episode(
        ctx,
        (DraftDiary(
            date=date, outcomes=outcomes, decisions=decisions,
            corrections=corrections, open_loops=open_loops, events=events,
        ),),
    )


def _items_json(*items: tuple[str, str, str]) -> str:
    """Build a daily items JSON response: ``(type, text, source)`` triples."""
    return json.dumps(
        {"items": [
            {"type": t, "text": x, "source": s} for (t, x, s) in items
        ]}
    )


def _daily(root: Path, date: str, body: str) -> None:
    MemoryStore(root).write_diary({
        "type": "diary", "date": date, "layer": "day", "period": date,
        "promoted_knowledge": [], "__body": body,
    })


def _daily_fm(root: Path, date: str) -> dict | None:
    path = root / "diary" / "daily" / f"{date}.md"
    if not path.exists():
        return None
    fm, _body = _split_frontmatter(path.read_text(encoding="utf-8"))
    return fm


# ---------- daily (slice-062 structured rewrite) ----------


def test_compress_daily_writes_fixed_markdown_and_frontmatter(tmp_path: Path) -> None:
    _structured_episode(
        tmp_path, "s1", outcomes=("完成了 daily 重写",),
        corrections=("原来以为 X -> 现确认 Y",),
        open_loops=("weekly 重写未做",),
    )
    provider = FakeProvider(_items_json(
        ("outcome", "完成了 daily 重写", "s1:0:end"),
        ("correction", "原来以为 X -> 现确认 Y", "s1:0:end"),
        ("open_loop", "weekly 重写未做", "s1:0:end"),
    ))
    assert compress_daily(tmp_path, "2026-07-01", provider) == "2026-07-01"
    [d] = MemoryStore(tmp_path).load_diary(layer="day")
    assert d.body.startswith("# 2026-07-01")
    assert "## 进展" in d.body
    assert "## 更正" in d.body
    assert "## 待续" in d.body
    assert "- 完成了 daily 重写" in d.body
    fm = _daily_fm(tmp_path, "2026-07-01")
    assert fm and fm["generation_status"] == "ok"
    assert fm["source_segments"] == ["s1:0:end"]
    assert fm["source_hash"]
    assert fm["generated_at"]


def test_compress_daily_no_episodes_returns_empty(tmp_path: Path) -> None:
    provider = FakeProvider()
    assert compress_daily(tmp_path, "2026-07-01", provider) == ""
    assert provider.calls == []  # no LLM call when nothing to compress
    assert not (tmp_path / "diary" / "daily" / "2026-07-01.md").exists()


def test_compress_daily_idempotent_skips_llm(tmp_path: Path) -> None:
    # contract 6: source_hash unchanged + existing daily valid -> no LLM call.
    _structured_episode(tmp_path, "s1", outcomes=("完成了 X",))
    provider = FakeProvider(_items_json(("outcome", "完成了 X", "s1:0:end")))
    compress_daily(tmp_path, "2026-07-01", provider)
    first_calls = len(provider.calls)
    first_body = MemoryStore(tmp_path).load_diary(layer="day")[0].body
    # rerun with a fresh provider -> no call, body unchanged
    provider2 = FakeProvider(_items_json(("outcome", "SHOULD NOT BE USED", "s1:0:end")))
    compress_daily(tmp_path, "2026-07-01", provider2)
    assert provider2.calls == []
    assert MemoryStore(tmp_path).load_diary(layer="day")[0].body == first_body
    assert first_calls >= 1


def test_daily_rebuild_scan_finds_missing_fallback_and_legacy_days(
    tmp_path: Path,
) -> None:
    """The next review must retry bad old dailies even with no new segment."""
    _structured_episode(
        tmp_path, "missing", date="2026-07-01", outcomes=("结果一",)
    )
    _structured_episode(
        tmp_path, "fallback", date="2026-07-02", outcomes=("结果二",)
    )
    _structured_episode(
        tmp_path, "legacy", date="2026-07-03", outcomes=("结果三",)
    )
    write_fallback_daily(tmp_path, "2026-07-02")
    _daily(tmp_path, "2026-07-03", "# 错写成前一天的旧版日记")
    legacy_path = tmp_path / "episodes" / "oldest.md"
    legacy_path.write_text(_dump_frontmatter({
        "type": "episode",
        "cc_session_id": "oldest",
        "workdir": "/tmp",
        "registered_at": "2026-07-04T10:00:00",
        "review_date": "2026-07-04",
        "source_jsonl": "/tmp/oldest.jsonl",
        "segments": [],
    }, "最老版本没有 activity_dates，也没有日期标题。"), encoding="utf-8")

    assert daily_dates_needing_rebuild(tmp_path) == [
        "2026-07-01",
        "2026-07-02",
        "2026-07-03",
        "2026-07-04",
    ]


def test_current_generation_version_is_not_rebuilt_when_sources_unchanged(
    tmp_path: Path,
) -> None:
    _structured_episode(tmp_path, "s1", outcomes=("完成结果",))
    provider = FakeProvider(_items_json(("outcome", "完成结果", "S1")))
    compress_daily(tmp_path, "2026-07-01", provider)

    fm = _daily_fm(tmp_path, "2026-07-01")
    assert fm and isinstance(fm.get("generation_version"), int)
    assert daily_dates_needing_rebuild(tmp_path) == []


def test_failed_version_upgrade_keeps_previous_usable_daily(
    tmp_path: Path,
) -> None:
    """A prompt/version migration must not replace good memory with fallback."""
    _structured_episode(tmp_path, "s1", outcomes=("已经存在的进展",))
    compress_daily(
        tmp_path,
        "2026-07-01",
        FakeProvider(_items_json(("outcome", "已经存在的进展", "S1"))),
    )
    path = tmp_path / "diary" / "daily" / "2026-07-01.md"
    fm, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    assert fm
    fm["generation_version"] = 1
    path.write_text(_dump_frontmatter(fm, body), encoding="utf-8")

    compress_daily(
        tmp_path,
        "2026-07-01",
        FakeProvider(raise_on=RuntimeError("provider down during migration")),
    )

    fm_after, body_after = _split_frontmatter(path.read_text(encoding="utf-8"))
    assert fm_after and fm_after["generation_status"] == "ok"
    assert fm_after["generation_version"] == 1
    assert body_after == body


def test_compress_daily_rebuilds_when_source_changes(tmp_path: Path) -> None:
    _structured_episode(tmp_path, "s1", outcomes=("旧结果",))
    FakeProvider2 = FakeProvider(_items_json(("outcome", "旧结果", "s1:0:end")))
    compress_daily(tmp_path, "2026-07-01", FakeProvider2)
    # a NEW segment lands for the same day -> source_hash changes -> rebuild
    _structured_episode(
        tmp_path, "s2", outcomes=("新结果",),
        segment_id="s2:0:end", registered_at="2026-07-01T12:00:00",
    )
    provider = FakeProvider(_items_json(("outcome", "新结果", "s2:0:end")))
    compress_daily(tmp_path, "2026-07-01", provider)
    assert provider.calls  # LLM was called again
    assert "新结果" in MemoryStore(tmp_path).load_diary(layer="day")[0].body


def test_compress_daily_retries_on_bad_source_then_succeeds(tmp_path: Path) -> None:
    # pass criteria: first response has an un-sourced item -> retry once -> ok.
    _structured_episode(tmp_path, "s1", outcomes=("真结果",))
    provider = FakeProvider(responses=[
        _items_json(("outcome", "真结果", "FAKE-SOURCE")),  # bad source
        _items_json(("outcome", "真结果", "s1:0:end")),     # good
    ])
    compress_daily(tmp_path, "2026-07-01", provider)
    assert len(provider.calls) == 2  # retried once
    assert "- 真结果" in MemoryStore(tmp_path).load_diary(layer="day")[0].body
    assert _daily_fm(tmp_path, "2026-07-01")["generation_status"] == "ok"


def test_compress_daily_retries_when_model_omits_source_progress(
    tmp_path: Path,
) -> None:
    """Structured outcomes require a 进展 item; omission is not a clean result."""
    _structured_episode(
        tmp_path,
        "s1",
        outcomes=("完成了真实实现",),
        corrections=("原判断已被更正",),
    )
    provider = FakeProvider(responses=[
        _items_json(("correction", "原判断已被更正", "S1")),
        _items_json(
            ("outcome", "完成了真实实现", "S1"),
            ("correction", "原判断已被更正", "S1"),
        ),
    ])

    compress_daily(tmp_path, "2026-07-01", provider)

    body = MemoryStore(tmp_path).load_diary(layer="day")[0].body
    assert len(provider.calls) == 2
    assert "## 进展" in body
    assert "## 更正" in body


def test_compress_daily_uses_short_source_aliases_for_llm_output(
    tmp_path: Path,
) -> None:
    """Production GLM repeatedly strips ``:start:end`` from long segment ids.

    The prompt must expose a short opaque alias and Python must resolve it back
    to the real segment id. This is based on the 2026-07-18 production fallback,
    where two retries returned the right session UUID but lost the byte suffix.
    """
    _structured_episode(tmp_path, "real-session-id", outcomes=("完成真实结果",))
    provider = FakeProvider(_items_json(
        ("outcome", "完成真实结果", "S1"),
    ))

    compress_daily(tmp_path, "2026-07-01", provider)

    body = MemoryStore(tmp_path).load_diary(layer="day")[0].body
    assert "完成真实结果" in body
    assert _daily_fm(tmp_path, "2026-07-01")["generation_status"] == "ok"
    assert "【segment S1】" in provider.calls[0][1]


def test_compress_daily_budget_drops_whole_items(tmp_path: Path) -> None:
    # contract 4 / C-3: over 800 chars -> drop WHOLE bullets, never mid-sentence.
    _structured_episode(tmp_path, "s1", outcomes=("a", "b"))
    big = "结果描述文本块" * 60  # ~420 chars each -> two bullets exceed the budget
    provider = FakeProvider(_items_json(
        ("outcome", big + "第一项标记ONE", "s1:0:end"),
        ("outcome", big + "第二项标记TWO", "s1:0:end"),
    ))
    compress_daily(tmp_path, "2026-07-01", provider)
    body = MemoryStore(tmp_path).load_diary(layer="day")[0].body
    assert len(body) <= 800
    # one whole bullet kept, the other wholly dropped — no half sentence
    assert ("ONE" in body) ^ ("TWO" in body)
    assert "…" not in body  # never an ellipsis truncation marker


def test_compress_daily_budget_keeps_correction_and_one_outcome(tmp_path: Path) -> None:
    # Extra progress is lower priority, but the section itself must survive.
    _structured_episode(tmp_path, "s1", outcomes=("r",), corrections=("c",))
    big = "结果描述文本块" * 40
    provider = FakeProvider(_items_json(
        ("outcome", big + "OUTCOME_ONE", "s1:0:end"),
        ("outcome", big + "OUTCOME_TWO", "s1:0:end"),
        ("correction", big + "CORRECTION_MARKER", "s1:0:end"),
    ))
    compress_daily(tmp_path, "2026-07-01", provider)
    body = MemoryStore(tmp_path).load_diary(layer="day")[0].body
    assert len(body) <= 800
    assert "CORRECTION_MARKER" in body
    assert ("OUTCOME_ONE" in body) ^ ("OUTCOME_TWO" in body)


def test_compress_daily_budget_does_not_starve_progress_section(
    tmp_path: Path,
) -> None:
    """A busy day must keep one item from every represented daily section.

    Real 2026-07-19/20 inputs contained outcomes, but the old priority loop
    deleted every outcome before touching repeated corrections/open loops.
    """
    _structured_episode(tmp_path, "s1", outcomes=("r",))
    long = "更正或待续的完整描述" * 28
    provider = FakeProvider(_items_json(
        ("outcome", "完成了当天关键实现并通过验证", "s1:0:end"),
        ("correction", long + "更正一", "s1:0:end"),
        ("correction", long + "更正二", "s1:0:end"),
        ("open_loop", long + "待续一", "s1:0:end"),
    ))

    compress_daily(tmp_path, "2026-07-01", provider)

    body = MemoryStore(tmp_path).load_diary(layer="day")[0].body
    assert len(body) <= 800
    assert "## 进展" in body
    assert "## 更正" in body
    assert "## 待续" in body


def test_compress_daily_omits_empty_sections(tmp_path: Path) -> None:
    # contract: an empty section is omitted entirely, not rendered empty.
    _structured_episode(tmp_path, "s1", outcomes=("只有进展",))
    provider = FakeProvider(_items_json(("outcome", "只有进展", "s1:0:end")))
    compress_daily(tmp_path, "2026-07-01", provider)
    body = MemoryStore(tmp_path).load_diary(layer="day")[0].body
    assert "## 进展" in body
    assert "## 更正" not in body
    assert "## 待续" not in body


def test_compress_daily_dedup_same_fact_keeps_all_sources(tmp_path: Path) -> None:
    # pass criteria: one fact repeated across segments -> one bullet, but
    # source_segments keeps every contributing segment.
    _structured_episode(
        tmp_path, "s1", outcomes=("完成了 X",),
        segment_id="s1:0:end", registered_at="2026-07-01T09:00:00",
    )
    _structured_episode(
        tmp_path, "s2", outcomes=("完成了 X",),
        segment_id="s2:0:end", registered_at="2026-07-01T10:00:00",
    )
    # the LLM merges semantically; here it returns two identical items (worst
    # case) — Python's exact dedup must still collapse to one bullet.
    provider = FakeProvider(_items_json(
        ("outcome", "完成了 X", "s1:0:end"),
        ("outcome", "完成了 X", "s2:0:end"),
    ))
    compress_daily(tmp_path, "2026-07-01", provider)
    body = MemoryStore(tmp_path).load_diary(layer="day")[0].body
    assert body.count("完成了 X") == 1  # one bullet
    fm = _daily_fm(tmp_path, "2026-07-01")
    assert set(fm["source_segments"]) == {"s1:0:end", "s2:0:end"}  # all sources


def test_compress_daily_fallback_on_provider_exception(tmp_path: Path) -> None:
    # contract 5 / C-7: provider failure -> fallback notice, NOT full aggregate.
    _structured_episode(
        tmp_path, "s1",
        outcomes=("一个真实结果" * 50,),  # would be a big aggregate if leaked
    )
    provider = FakeProvider(raise_on=RuntimeError("provider down"))
    compress_daily(tmp_path, "2026-07-01", provider)
    body = MemoryStore(tmp_path).load_diary(layer="day")[0].body
    fm = _daily_fm(tmp_path, "2026-07-01")
    assert fm["generation_status"] == "fallback"
    # the raw experience must NOT be dumped into the daily body (C-7)
    assert "一个真实结果" not in body
    assert "s1:0:end" in body  # fallback points at the source segment


def test_compress_daily_fallback_on_bad_json_twice(tmp_path: Path) -> None:
    _structured_episode(tmp_path, "s1", outcomes=("x",))
    provider = FakeProvider(responses=["not json at all", "{still not json"])
    compress_daily(tmp_path, "2026-07-01", provider)
    assert len(provider.calls) == 2  # retried once
    assert _daily_fm(tmp_path, "2026-07-01")["generation_status"] == "fallback"


def test_compress_daily_legacy_events_episode_still_works(tmp_path: Path) -> None:
    # a legacy free-text episode (no structured lists) still feeds the daily.
    _structured_episode(tmp_path, "s1", events="卡两小时在浏览器缓存")
    provider = FakeProvider(_items_json(
        ("open_loop", "浏览器缓存问题待复测", "s1:0:end"),
    ))
    compress_daily(tmp_path, "2026-07-01", provider)
    assert "浏览器缓存" in provider.calls[0][1]  # legacy text reached the LLM


def test_write_fallback_daily_no_episodes_is_noop(tmp_path: Path) -> None:
    # the no-LLM path writes nothing when there is nothing to summarize.
    assert write_fallback_daily(tmp_path, "2026-07-01") == ""
    assert not (tmp_path / "diary" / "daily" / "2026-07-01.md").exists()


def test_write_fallback_daily_marks_status_and_points_at_sources(tmp_path: Path) -> None:
    _structured_episode(tmp_path, "s1", outcomes=("x",))
    assert write_fallback_daily(tmp_path, "2026-07-01") == "2026-07-01"
    fm = _daily_fm(tmp_path, "2026-07-01")
    assert fm["generation_status"] == "fallback"
    assert fm["source_segments"] == ["s1:0:end"]


def test_compress_daily_single_oversized_item_falls_back(tmp_path: Path) -> None:
    """C-3: a single item larger than the 800-char budget can't be trimmed
    without mid-sentence truncation, so the day falls back — never an
    over-budget "ok" daily (which would also defeat idempotence and re-call
    the LLM on every rerun)."""
    _structured_episode(tmp_path, "s1", outcomes=("x",))
    huge = "巨" * 900  # one item alone exceeds the whole budget
    provider = FakeProvider(_items_json(("outcome", huge + "MARKER", "s1:0:end")))
    compress_daily(tmp_path, "2026-07-01", provider)
    assert _daily_fm(tmp_path, "2026-07-01")["generation_status"] == "fallback"


# ---------- weekly ----------


def test_compress_weekly_writes_weekly_and_bypass(tmp_path: Path) -> None:
    # two dailies in the same ISO week (2026-W28: 2026-07-06..07-12)
    _daily(tmp_path, "2026-07-06", "day A events with gotcha")
    _daily(tmp_path, "2026-07-08", "day B events")
    provider = FakeProvider("## 周记\n本周主线...")
    report = compress_weekly(tmp_path, "2026-W28", provider)
    assert report["weekly_written"]
    weeklies = MemoryStore(tmp_path).load_diary(layer="week")
    assert any(w.period == "2026-W28" for w in weeklies)


def test_compress_weekly_no_dailies_returns_empty(tmp_path: Path) -> None:
    provider = FakeProvider()
    report = compress_weekly(tmp_path, "2026-W28", provider)
    assert report["weekly_written"] is False
    assert provider.calls == []


def test_compress_weekly_only_matches_iso_week(tmp_path: Path) -> None:
    # 2026-07-05 is Sunday of W27; 2026-07-06 is Monday of W28
    _daily(tmp_path, "2026-07-05", "W27_SUNDAY")
    _daily(tmp_path, "2026-07-06", "W28_MONDAY")
    provider = FakeProvider("周记")
    compress_weekly(tmp_path, "2026-W28", provider)
    assert "W28_MONDAY" in provider.calls[0][1]
    assert "W27_SUNDAY" not in provider.calls[0][1]


def test_compress_weekly_writes_bypass_files(tmp_path: Path) -> None:
    # C-2: three bypass categories land in diary/bypass/<cat>/<iso_week>.md;
    # empty categories produce no file.
    _daily(tmp_path, "2026-07-06", "tech detail + emotional scene")
    out = json.dumps({"weekly": "周记正文", "bypass": {
        "technical-detail": "技术细节正文",
        "emotional-trigger": "情感场景正文",
        "cross-week-causal": "",  # empty → no file
    }})
    report = compress_weekly(tmp_path, "2026-W28", FakeProvider(out))
    assert report["weekly_written"]
    assert report["bypass"]["technical-detail"] is True
    assert report["bypass"]["emotional-trigger"] is True
    assert report["bypass"]["cross-week-causal"] is False
    bp = tmp_path / "diary" / "bypass"
    assert (bp / "technical-detail" / "2026-W28.md").exists()
    assert (bp / "emotional-trigger" / "2026-W28.md").exists()
    assert not (bp / "cross-week-causal" / "2026-W28.md").exists()
    [w] = [d for d in MemoryStore(tmp_path).load_diary(layer="week")
           if d.period == "2026-W28"]
    assert w.body == "周记正文"


def test_compress_weekly_non_json_falls_back_to_weekly_only(tmp_path: Path) -> None:
    # a non-JSON LLM response degrades to weekly-only (raw text as weekly body)
    _daily(tmp_path, "2026-07-06", "events")
    report = compress_weekly(tmp_path, "2026-W28", FakeProvider("just weekly text, no JSON"))
    assert report["weekly_written"]
    assert all(v is False for v in report["bypass"].values())
    [w] = [d for d in MemoryStore(tmp_path).load_diary(layer="week")
           if d.period == "2026-W28"]
    assert w.body == "just weekly text, no JSON"


def test_parse_weekly_empty_weekly_keeps_bypass_isolated() -> None:
    """W5 (codex): valid JSON with empty weekly must NOT fall back to raw —
    that would leak bypass bodies into the weekly diary (C-2 isolation)."""
    raw = (
        '{"weekly": "", "bypass": {"technical-detail": "td body", '
        '"emotional-trigger": "et body", "cross-week-causal": "cc body"}}'
    )
    out = _parse_weekly_output(raw)
    assert out["weekly"] == ""  # not the raw JSON
    assert "td body" not in out["weekly"]  # bypass not leaked into weekly
    assert out["bypass"]["technical-detail"] == "td body"
    assert out["bypass"]["emotional-trigger"] == "et body"
    assert out["bypass"]["cross-week-causal"] == "cc body"


def test_parse_weekly_non_json_uses_raw_fallback() -> None:
    """W5: only when the response is not JSON at all do we use raw as weekly."""
    out = _parse_weekly_output("just plain text, no json braces")
    assert out["weekly"] == "just plain text, no json braces"
    assert out["bypass"] == {}


# ---------- W4 (codex): weekly/monthly hard 800-char cap on persist ----------
# (daily stopped hard-capping in slice-052 C-2; weekly/monthly still do — span更大)


def test_compress_weekly_caps_long_weekly(tmp_path: Path) -> None:
    """W4: weekly body is hard-capped on persist."""
    _daily(tmp_path, "2026-07-06", "day body")
    long_weekly = "周" * 1200
    raw = json.dumps({"weekly": long_weekly, "bypass": {}})
    compress_weekly(tmp_path, "2026-W28", FakeProvider(raw))
    [w] = [d for d in MemoryStore(tmp_path).load_diary(layer="week")
           if d.period == "2026-W28"]
    assert len(w.body) <= 800


def test_compress_monthly_caps_long_output(tmp_path: Path) -> None:
    """W4: monthly body is hard-capped on persist."""
    MemoryStore(tmp_path).write_diary({
        "type": "diary", "date": "2026-W28", "layer": "week",
        "period": "2026-W28", "promoted_knowledge": [], "__body": "weekly body",
    })
    long_body = "月" * 1200
    compress_monthly(tmp_path, "2026-07", FakeProvider(long_body))
    [m] = [d for d in MemoryStore(tmp_path).load_diary(layer="month")
           if d.period == "2026-07"]
    assert len(m.body) <= 800
