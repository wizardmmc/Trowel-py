"""提供提炼 Draft 的稳定数据模型、兼容解析和落盘前检查入口。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from trowel_py.memory.draft.parser import parse_diary as _run_parse_diary
from trowel_py.memory.draft.parser import parse_draft as _run_parse_draft
from trowel_py.memory.draft.parser import parse_note as _run_parse_note
from trowel_py.memory.draft.parser import str_list as _run_str_list
from trowel_py.memory.draft.validation import (
    procedure_warnings as _run_procedure_warnings,
)
from trowel_py.memory.draft.validation import validate_draft as _run_validation
from trowel_py.memory.draft.episode import (
    DraftCorrection as DraftCorrection,
    DraftDecision as DraftDecision,
    DraftEpisodeItem as DraftEpisodeItem,
    DraftEvidence as DraftEvidence,
    DraftOpenLoop as DraftOpenLoop,
    DraftOutcome as DraftOutcome,
    episode_item_text as episode_item_text,
    episode_item_to_dict as episode_item_to_dict,
    parse_episode_item as parse_episode_item,
    project_episode_items as project_episode_items,
)
from trowel_py.memory.prompt import (
    NOTE_KINDS,
    VERIFICATION_TIERS,
)


@dataclass(frozen=True)
class DraftNote:
    """记录提炼 agent 产出的一条候选知识。

    Attributes:
        title: 候选知识的标题；落盘前必须包含非空文本。
        summary: 用于索引和检索的简短摘要。
        body: 候选知识的详细正文。
        tags: 用于检索的标签。
        kind: 知识类别；落盘前必须属于 ``NOTE_KINDS``。
        verification: 证据强度；落盘前必须属于 ``VERIFICATION_TIERS``。
        verification_reason: 选择该证据强度的理由。
        pain: 问题造成的损失或成本评分；本模型不限制范围。
        pain_reason: 给出损失或成本评分的理由。
        conflicts_with: 与当前候选结论冲突的现有 Note ID。
    """

    title: str
    summary: str = ""
    body: str = ""
    tags: tuple[str, ...] = ()
    kind: str = "fact"
    verification: str = "inferred-untested"
    verification_reason: str = ""
    pain: int = 0
    pain_reason: str = ""
    conflicts_with: tuple[str, ...] = ()


@dataclass(frozen=True)
class DraftDiary:
    """记录一天的结构化 Episode 事件及旧格式兼容字段。

    ``items`` 是新草稿的写入契约。四类文本列表和 ``events`` 用于兼容旧记录，
    Episode 写入器仍可持久化它们。新的 agent JSON 不得混用 ``items`` 与旧
    字段；没有 ``items`` 的新草稿若携带旧字段，硬校验会拒绝。``items`` 非空
    时会补齐其中尚为空的四类投影，但不会覆盖调用方显式提供的非空列表。

    Attributes:
        date: 这些经历所属的日期文本；落盘前必须非空。
        outcomes: 旧调用方读取的结果文本投影。
        decisions: 旧调用方读取的 active 决策文本投影。
        corrections: 旧调用方读取的更正文本投影。
        open_loops: 旧调用方读取的 active 待续事项文本投影。
        events: 旧版自由文本经历；新草稿不允许使用。
        items: 新版 kind-specific Episode 事件。
    """

    date: str
    outcomes: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    corrections: tuple[str, ...] = ()
    open_loops: tuple[str, ...] = ()
    events: str = ""
    items: tuple[DraftEpisodeItem, ...] = ()

    def __post_init__(self) -> None:
        """用结构化事件补齐尚为空的四类旧格式文本投影。

        投影只包含 outcome、active decision、correction 和 active open loop；
        evidence、superseded decision 与 closed open loop 只保留在 ``items``。
        """
        if not self.items:
            return
        outcomes, decisions, corrections, open_loops = project_episode_items(self.items)
        if not self.outcomes:
            object.__setattr__(self, "outcomes", outcomes)
        if not self.decisions:
            object.__setattr__(self, "decisions", decisions)
        if not self.corrections:
            object.__setattr__(self, "corrections", corrections)
        if not self.open_loops:
            object.__setattr__(self, "open_loops", open_loops)

    def all_items(self) -> list[str]:
        """返回这一天用于展示或审计的经历文本。

        ``items`` 非空时渲染其中全部结构化事件，包括不进入旧投影的 evidence
        和非 active 项，并忽略旧列表；否则按 outcomes、decisions、
        corrections、open_loops 的顺序拼接旧文本。两个分支都不返回
        ``events``，需要它的调用方会另行追加。

        Returns:
            保持原有事件或列表顺序的文本列表。
        """
        if self.items:
            return [episode_item_text(item) for item in self.items]
        return [
            *self.outcomes,
            *self.decisions,
            *self.corrections,
            *self.open_loops,
        ]


@dataclass(frozen=True)
class Draft:
    """汇总一次提炼生成的候选知识、经历和人工升级请求。

    Attributes:
        notes: 本次提炼发现的候选知识，用于创建或合并 Note。
        diary: 按日期组织的候选经历，用于写入来源会话的 Episode；Daily 由
            Episode 继续派生。
        reflection: 对本次任务是否正确利用已有记忆的反思；非空时写入
            ``meta/reflections``。
        escalate_to_human: 已尝试自行解决但仍需人工判断的问题；丢弃纯空白项后
            写入 ``meta/escalations``。
    """

    notes: tuple[DraftNote, ...] = ()
    diary: tuple[DraftDiary, ...] = ()
    reflection: str = ""
    escalate_to_human: tuple[str, ...] = ()


def parse_draft(text: str) -> Draft:
    """按新旧兼容规则解析 agent 输出的 Draft JSON。

    Note、旧 Diary 和顶层兼容字段沿用宽松类型转换；结构化 ``items`` 按严格
    结构解析。本函数不执行落盘前硬校验，调用方须另行调用
    ``validate_draft``。

    Args:
        text: agent 返回的完整 JSON 文本。

    Returns:
        解析出的冻结 Draft。

    Raises:
        json.JSONDecodeError: 文本不是合法 JSON。
        AttributeError: JSON 顶层或 Note、Diary、Episode 项不是对象。
        TypeError: 顶层集合或 Note、结构化 Diary、Episode 字段无法按兼容规则
            解析。
        ValueError: ``pain`` 无法转换为整数，或结构化 Diary、Episode 项不符合
            严格结构。
        OverflowError: ``pain`` 是无法转换为整数的无穷浮点值。
    """
    return _run_parse_draft(
        text,
        loads=json.loads,
        draft_type=Draft,
        parse_note=_parse_note,
        parse_diary=_parse_diary,
    )


def validate_draft(draft: Draft) -> list[str]:
    """收集会阻止整份 Draft 落盘的字段错误。

    错误按 Note、Diary 和 Episode 项的遍历顺序累积；调用方只应在返回空列表时
    持久化，不能跳过错误项后部分落盘。

    Args:
        draft: 要检查的完整提炼草稿。
    Returns:
        稳定顺序的错误文本；空列表表示通过硬校验。
    """
    return _run_validation(
        draft,
        note_kinds=NOTE_KINDS,
        verification_tiers=VERIFICATION_TIERS,
    )


_PROCEDURE_ELEMENTS: dict[str, tuple[str, ...]] = {
    "trigger": ("trigger", "触发", "场景是", "什么场景"),
    "procedure": ("procedure", "做法", "步骤", "怎么做"),
    "stop": ("stop", "何时停", "停止条件", "终止"),
    "anti-pattern": ("anti-pattern", "anti pattern", "别做", "不要", "反面"),
}


def procedure_warnings(draft: Draft) -> list[str]:
    """报告 procedure Note 可能缺少的过程要素。

    检查以不区分大小写的子串匹配识别触发场景、步骤、停止条件和反面做法；
    空正文只产生一条专用告警并跳过四要素检查。结果只用于告警，不阻止草稿
    落盘。

    Args:
        draft: 要检查的完整提炼草稿。

    Returns:
        按 Note 和过程要素顺序排列的告警文本。
    """
    return _run_procedure_warnings(draft, elements=_PROCEDURE_ELEMENTS)


def _parse_note(n: dict[str, Any]) -> DraftNote:
    """按宽松兼容规则把字段映射解析为候选知识。

    Args:
        n: agent 输出的一条 Note 字段映射。
    """
    return _run_parse_note(n, note_type=DraftNote)


def _parse_diary(d: dict[str, Any]) -> DraftDiary:
    """把新旧格式字段映射解析为一天的候选经历。

    ``items`` 值不为 None 时必须是列表，且 Diary 顶层只能包含 ``date`` 和
    ``items``；值为 None 时按旧格式解析，旧列表按宽松字符串规则读取。

    Args:
        d: agent 输出的一条 Diary 字段映射。
    """
    return _run_parse_diary(
        d,
        diary_type=DraftDiary,
        str_list=_str_list,
        parse_episode_item=parse_episode_item,
    )


def _str_list(value: Any) -> tuple[str, ...]:
    """把旧格式列表中的值转换为非空字符串元组。

    列表元素先经过 ``str()``，再去除首尾空白；转换后为空的元素才会丢弃。

    Args:
        value: 旧 Diary 字段值；非列表值按空列表处理。
    """
    return _run_str_list(value)
