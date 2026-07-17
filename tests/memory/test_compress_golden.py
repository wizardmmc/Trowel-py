"""slice-062 golden test: the real daily pipeline over real 2026-07-17 episodes.

Copies the real ``~/.trowel/memory/episodes`` entries that cover 2026-07-17 into
a temp root (never writes the real memory), then runs ``compress_daily`` with a
provider that simulates an obedient LLM (clean structured items citing the real
source segments). Asserts the structural promises that hold regardless of the
LLM's exact wording — the ≤800-char budget, the fixed 进展/更正/待续 shape, full
source provenance, and that agent self-evaluation never reaches the body.

The exact daily wording is reviewed by a human (spec: "预期结构由人 review，不把
LLM 原文硬编码成唯一答案"); this test pins the pipeline invariants, not the prose.

Marked ``integration`` so the default suite stays repo-self-contained: it reads
the developer's live ``~/.trowel/memory`` (private, machine-specific), so it only
runs when invoked explicitly — ``CC_INTEGRATION=1 pytest -m integration`` — or
on a nightly golden review, never as a CI gate.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from trowel_py.memory.compress import compress_daily
from trowel_py.memory.store import MemoryStore, _split_frontmatter

_REAL_EPISODES = Path.home() / ".trowel" / "memory" / "episodes"
_TARGET_DATE = "2026-07-17"


def _covering_episodes() -> list[Path]:
    """Real episode files whose activity_dates include the target date."""
    if not _REAL_EPISODES.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(_REAL_EPISODES.glob("*.md")):
        fm, _body = _split_frontmatter(p.read_text(encoding="utf-8"))
        if not fm:
            continue
        dates = fm.get("activity_dates") or []
        if any(str(d) == _TARGET_DATE for d in dates):
            out.append(p)
    return out


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _covering_episodes(),
        reason=f"no real {_TARGET_DATE} episodes under {_REAL_EPISODES}",
    ),
]


class _ObedientProvider:
    """Simulates an obedient LLM: clean structured items citing the first real
    segment. The item set is deliberately large (>800 chars) so the budget
    selector must drop whole low-priority bullets."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        marker = "【segment "
        seg = "unknown-seg"
        i = user_prompt.find(marker)
        if i >= 0:
            seg = user_prompt[i + len(marker):].split("】", 1)[0]
        items: list[tuple[str, str, str]] = [
            ("outcome", "完成了 slice-062 daily 重写的结构化管线", seg),
            ("outcome", "验证全量 memory 测试零回归", seg),
            ("decision", "结构化四列表渲染进 episode 正文，renderer/parser 闭环", seg),
            ("correction", "原来 daily 把全量 aggregate 当摘要 -> 改成 fallback 短提示", seg),
            ("correction", "原来按 review_date 聚合 -> 改成按 segment activity_dates 精确投影", seg),
            ("open_loop", "weekly/monthly 表达重写未做，只消费新合格 daily", seg),
            ("open_loop", "真实 2026-07-17 daily 的 golden review 待人确认", seg),
        ]
        # filler outcomes (low priority) to push the body past the 800 budget
        for n in range(8):
            items.append(
                ("outcome", f"支撑细节项{n}：次要的、可被预算删减的结果描述文本块", seg)
            )
        return json.dumps(
            {"items": [{"type": t, "text": x, "source": s} for t, x, s in items]}
        )


def test_real_2026_07_17_daily_under_budget_with_structure(tmp_path: Path) -> None:
    mem = tmp_path / "memory"
    (mem / "episodes").mkdir(parents=True)
    for src in _covering_episodes():
        shutil.copy2(src, mem / "episodes" / src.name)

    provider = _ObedientProvider()
    assert compress_daily(mem, _TARGET_DATE, provider) == _TARGET_DATE

    [d] = MemoryStore(mem).load_diary(layer="day")
    assert d.date == _TARGET_DATE

    # C-3: body (title + section headers + bullets) fits the 800-char budget.
    assert len(d.body) <= 800, f"daily body {len(d.body)} chars:\n{d.body}"

    # contract 2: fixed shape, title + at least the high-priority sections kept.
    assert d.body.startswith(f"# {_TARGET_DATE}")
    assert "## 更正" in d.body   # corrections: high priority, survives the trim
    assert "## 待续" in d.body   # open loops: high priority, survives the trim

    # C-5: agent self-evaluation never reaches the body.
    for banned in ("表现不错", "认真检查", "全程高价值", "绩效", "反复确认"):
        assert banned not in d.body

    # C-3 (cont): never a mid-sentence ellipsis truncation.
    assert "…" not in d.body

    # C-6 / contract 6: full source provenance — every contributing segment.
    fm, _body = _split_frontmatter(
        (mem / "diary" / "daily" / f"{_TARGET_DATE}.md").read_text(encoding="utf-8")
    )
    assert fm["generation_status"] == "ok"
    assert fm["source_hash"]
    real_seg_count = len(_covering_episodes())
    assert len(fm["source_segments"]) >= real_seg_count


def test_real_daily_idempotent_rerun_skips_llm(tmp_path: Path) -> None:
    # contract 6: unchanged inputs -> second run does not call the provider.
    mem = tmp_path / "memory"
    (mem / "episodes").mkdir(parents=True)
    for src in _covering_episodes():
        shutil.copy2(src, mem / "episodes" / src.name)

    provider = _ObedientProvider()
    compress_daily(mem, _TARGET_DATE, provider)
    first_body = MemoryStore(mem).load_diary(layer="day")[0].body
    first_calls = len(provider.calls)

    provider2 = _ObedientProvider()
    compress_daily(mem, _TARGET_DATE, provider2)
    assert provider2.calls == []  # no LLM call
    assert len(provider.calls) == first_calls
    assert MemoryStore(mem).load_diary(layer="day")[0].body == first_body
