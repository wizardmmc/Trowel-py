"""Monthly 文本生成的完整来源分层与输出预算校验。"""

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
    heading: str
    units: list[str] = field(default_factory=list)


def _sentence_units(line: str) -> list[str]:
    units = [part.strip() for part in re.split(r"(?<=[。！？!?；;])", line)]
    return [part for part in units if part]


def _sections(raw: str) -> list[_Section]:
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
    sections = _sections(raw)
    selected: set[tuple[int, int]] = set()

    # heading 定义相互独立的主线；先保每个 section 一条，再按原顺序补齐。
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
    body = "\n\n".join(
        f"## source_week: {source.period}\n{source.body.strip()}" for source in sources
    )
    return MONTHLY_USER_PROMPT.format(body=body)


def _valid_output(raw: str) -> str | None:
    text = raw.strip()
    if not text:
        return None
    if len(text) <= MONTHLY_BUDGET:
        return text + "\n"
    return _select_complete_units(text)


def _complete(provider: LLMProvider, prompt: str) -> str | None:
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
