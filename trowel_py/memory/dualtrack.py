"""Daily review draft 的知识/经历轨泄漏审计，只报告而不迁移内容。"""

from __future__ import annotations

from dataclasses import dataclass

from trowel_py.memory.draft import Draft
from trowel_py.memory.prompt import DUALTRACK_SIGNAL_WORDS

# 命中词两侧保留的上下文字符数。
_SNIPPET_RADIUS = 15


@dataclass(frozen=True)
class DiaryLeak:
    date: str
    signal: str
    snippet: str


@dataclass(frozen=True)
class DualtrackReport:
    leaks: tuple[DiaryLeak, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.leaks


def audit_draft(draft: Draft) -> DualtrackReport:
    """只扫描经历轨的结构化项与 legacy events，每条最多报告首个 signal。

    notes 属于知识轨，其中出现 signal 是合法内容，不参与审计。
    """
    leaks: list[DiaryLeak] = []
    for d in draft.diary:
        text = "\n".join([*d.all_items(), d.events])
        for sig in DUALTRACK_SIGNAL_WORDS:
            idx = text.find(sig)
            if idx != -1:
                start = max(0, idx - _SNIPPET_RADIUS)
                end = min(len(text), idx + len(sig) + _SNIPPET_RADIUS)
                leaks.append(
                    DiaryLeak(date=d.date, signal=sig, snippet=text[start:end])
                )
                break
    return DualtrackReport(leaks=tuple(leaks))
