"""以信号词审计 Daily review 草稿中的知识轨内容泄漏。"""

from __future__ import annotations

from dataclasses import dataclass

from trowel_py.memory.draft import Draft
from trowel_py.memory.prompt import DUALTRACK_SIGNAL_WORDS

# 命中片段在信号词两侧各保留的最大字符数。
_SNIPPET_RADIUS = 15


@dataclass(frozen=True)
class DiaryLeak:
    """记录一天经历中疑似属于知识轨的信号。

    Attributes:
        date: 被标记的 Diary 日期。
        signal: 命中的知识轨信号词。
        snippet: 命中词及其两侧的截取上下文。
    """

    date: str
    signal: str
    snippet: str


@dataclass(frozen=True)
class DualtrackReport:
    """汇总一份草稿的经历轨泄漏信号。

    Attributes:
        leaks: 按 Diary 顺序记录的疑似泄漏；每个 Diary 至多一条。
    """

    leaks: tuple[DiaryLeak, ...] = ()

    @property
    def clean(self) -> bool:
        """返回是否没有发现任何泄漏信号。"""
        return not self.leaks


def audit_draft(draft: Draft) -> DualtrackReport:
    """报告经历文本中疑似属于知识轨的信号，不阻断落盘，也不迁移内容。

    ``items`` 非空时扫描全部 v2 事件，包括 evidence、已替代的决策和已关闭
    的待续事项，并忽略四类旧列表；否则按 outcomes、decisions、corrections
    和 open_loops 的顺序拼接四类旧文本。两种情况都会另行追加旧
    ``events``，用换行拼成当天的待查文本。函数进行区分大小写的子串搜索，
    按 ``DUALTRACK_SIGNAL_WORDS`` 的配置顺序选取首个存在的信号，而非正文
    中位置最靠前的信号，并截取命中词两侧各至多 15 个字符。Note 本就属于
    知识轨，不参与审计。

    Args:
        draft: 要审计的 Daily review 草稿。

    Returns:
        只含诊断信息的报告；每个 Diary 至多产生一条泄漏记录。
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
