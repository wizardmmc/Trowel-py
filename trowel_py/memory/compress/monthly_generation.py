"""将完整 Weekly 来源分批生成 Monthly，并按完整文本单元控制输出预算。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from trowel_py.llm.client import LLMProvider

from .rollup_sources import PeriodSource

INPUT_BUDGET = 8000
MONTHLY_BUDGET = 800
MONTHLY_GENERATION_VERSION = 2
MONTHLY_SYSTEM_PROMPT = (
    "你是月记压缩器。把给出的周记压缩成不超过 800 字的完整月记。"
    "保留主线事件流，不从句子中间截断。只输出月记正文，不要解释。"
)
MONTHLY_USER_PROMPT = (
    "本月周记（按周序）：\n{body}\n\n输出完整月记（不超过 800 字）："
)

logger = logging.getLogger("trowel_py.memory.compress")


@dataclass
class _Section:
    """记录 Monthly 输出的一个标题分区。

    Attributes:
        heading: Markdown 标题；正文出现在首个标题前时为空字符串。
        units: 该分区按句末标点拆出的完整文本单元。
    """

    heading: str
    units: list[str] = field(default_factory=list)


def _sentence_units(line: str) -> list[str]:
    """在句末标点后拆分一行；没有句末标点时整行作为一个文本单元。"""
    units = [part.strip() for part in re.split(r"(?<=[。！？!?；;])", line)]
    return [part for part in units if part]


def _sections(raw: str) -> list[_Section]:
    """按 Markdown 标题划分 Monthly 文本，并把各分区正文拆成完整文本单元。"""
    sections = [_Section("")]
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^#{1,6}\s+", stripped):
            sections.append(_Section(stripped))
            continue
        sections[-1].units.extend(_sentence_units(stripped))
    return [section for section in sections if section.heading or section.units]


def _render_selected(
    sections: list[_Section],
    selected: set[tuple[int, int]],
) -> str:
    """按原顺序渲染选中的文本单元，并仅保留仍有正文的标题。"""
    lines: list[str] = []
    for section_index, section in enumerate(sections):
        units = [
            unit
            for unit_index, unit in enumerate(section.units)
            if (section_index, unit_index) in selected
        ]
        if not units:
            continue
        if section.heading:
            lines.append(section.heading)
        lines.extend(units)
    return "\n".join(lines) + ("\n" if lines else "")


def _select_complete_units(raw: str) -> str | None:
    """在字符预算内选择完整文本单元，并优先为每个分区保留第一个单元。

    如果没有任何文本单元能连同其标题放入预算，则返回 None。
    """
    sections = _sections(raw)
    selected: set[tuple[int, int]] = set()

    # 标题划分不同分区；先尝试各保留第一个单元，再按原顺序补充其余单元。
    candidates = [
        (section_index, 0)
        for section_index, section in enumerate(sections)
        if section.units
    ]
    candidates.extend(
        (section_index, unit_index)
        for section_index, section in enumerate(sections)
        for unit_index in range(1, len(section.units))
    )
    for candidate in candidates:
        rendered = _render_selected(sections, selected | {candidate})
        if len(rendered) <= MONTHLY_BUDGET:
            selected.add(candidate)
    rendered = _render_selected(sections, selected)
    return rendered or None


def _prompt(sources: list[PeriodSource]) -> str:
    """按周序将完整 Weekly 正文渲染为 Monthly 模型输入。"""
    body = "\n\n".join(
        f"## source_week: {source.period}\n{source.body.strip()}" for source in sources
    )
    return MONTHLY_USER_PROMPT.format(body=body)


def _valid_output(raw: str) -> str | None:
    """规范化非空模型输出，超出预算时只保留能完整放入的文本单元。"""
    text = raw.strip()
    if not text:
        return None
    if len(text) <= MONTHLY_BUDGET:
        return text + "\n"
    return _select_complete_units(text)


def _complete(provider: LLMProvider, prompt: str) -> str | None:
    """调用 Monthly 模型，输出为空或无法按完整文本单元压入预算时重试一次。

    任一次模型调用抛出异常都会立即返回 None，不再重试。
    """
    error = ""
    for attempt in range(2):
        try:
            raw = provider.complete(MONTHLY_SYSTEM_PROMPT, prompt + error)
        except Exception:  # noqa: BLE001
            logger.warning("monthly provider call failed", exc_info=True)
            return None
        if (valid := _valid_output(raw)) is not None:
            return valid
        if attempt == 0:
            error = "\n\n上次输出为空或超过 800 字。请重新压缩，保留完整句且不超过 800 字。"
    return None


def _chunks(sources: list[PeriodSource]) -> list[list[PeriodSource]]:
    """按输入预算把 Weekly 来源分成连续批次，不截断单个来源。"""
    chunks: list[list[PeriodSource]] = []
    current: list[PeriodSource] = []
    for source in sources:
        candidate = current + [source]
        if current and len(_prompt(candidate)) > INPUT_BUDGET:
            chunks.append(current)
            current = [source]
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def generate_monthly(
    provider: LLMProvider,
    sources: list[PeriodSource],
) -> str | None:
    """从非空 Weekly 来源生成预算内的 Monthly 正文。

    多批来源先分别压缩，再把各批摘要合并为最终 Monthly；任一阶段失败都会返回
    None。

    Args:
        provider: 提供文本补全的模型客户端。
        sources: 按周序排列的非空 Weekly 来源。

    Returns:
        以换行结尾的 Monthly 正文；生成或预算校验失败时返回 None。
    """
    chunks = _chunks(sources)
    if len(chunks) == 1:
        return _complete(provider, _prompt(chunks[0]))
    summaries: list[PeriodSource] = []
    for index, chunk in enumerate(chunks, 1):
        summary = _complete(provider, _prompt(chunk))
        if summary is None:
            return None
        summaries.append(PeriodSource(f"chunk-{index}", summary, {}))
    return _complete(provider, _prompt(summaries))
