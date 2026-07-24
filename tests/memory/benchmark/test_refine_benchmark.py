"""需要真实 CC 提炼 agent 的质量 benchmark，默认不在 CI 中运行。

显式运行：

    .venv/bin/python -m pytest -m benchmark tests/memory/benchmark/

必须在独立终端接入真实 agent 和会话录制，不能嵌套在交互式 Claude 会话中（#46416）。
"""

import pytest

pytestmark = pytest.mark.benchmark


def test_s4_false_conclusion_flagged_inferred_untested() -> None:
    """未经实测且后来被推翻的根因不能标记为 ``verified``。"""
    pytest.skip(
        "benchmark: wire a real CCHost + S4 fixture transcript; see "
        "docs/milestones/spike-s1/s4-refine-benchmark.md"
    )


def test_dualtrack_split_no_knowledge_in_diary() -> None:
    """知识内容进入 notes，经历事件进入 diary。"""
    pytest.skip("benchmark: wire real CCHost + june-diary fixture")


def test_conflict_with_existing_note_marked() -> None:
    """冲突的新 note 必须记录 ``conflicts_with``，不能覆盖旧 note。"""
    pytest.skip("benchmark: wire real CCHost + conflicting-notes fixture")


def test_pain_rank_monotonic() -> None:
    """损失严重度递增时，pain 排名应保持单调，而非要求精确分值。"""
    pytest.skip("benchmark: wire real CCHost + graded-severity fixtures (S6 pending)")
