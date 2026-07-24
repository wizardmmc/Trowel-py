"""Episode v2 的 kind-specific 值对象与投影。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, TypeAlias


@dataclass(frozen=True)
class DraftOutcome:
    summary: str
    detail: str
    source_refs: tuple[str, ...]
    kind: Literal["outcome"] = field(default="outcome", init=False)


@dataclass(frozen=True)
class DraftDecision:
    summary: str
    reason: str
    status: str
    source_refs: tuple[str, ...]
    kind: Literal["decision"] = field(default="decision", init=False)


@dataclass(frozen=True)
class DraftCorrection:
    before: str
    after: str
    reason: str
    source_refs: tuple[str, ...]
    kind: Literal["correction"] = field(default="correction", init=False)


@dataclass(frozen=True)
class DraftOpenLoop:
    summary: str
    reason: str
    status: str
    source_refs: tuple[str, ...]
    kind: Literal["open_loop"] = field(default="open_loop", init=False)


@dataclass(frozen=True)
class DraftEvidence:
    summary: str
    detail: str
    source_refs: tuple[str, ...]
    kind: Literal["evidence"] = field(default="evidence", init=False)


DraftEpisodeItem: TypeAlias = (
    DraftOutcome | DraftDecision | DraftCorrection | DraftOpenLoop | DraftEvidence
)

_ITEM_KEYS: dict[str, set[str]] = {
    "outcome": {"kind", "summary", "detail", "source_refs"},
    "decision": {"kind", "summary", "reason", "status", "source_refs"},
    "correction": {"kind", "before", "after", "reason", "source_refs"},
    "open_loop": {"kind", "summary", "reason", "status", "source_refs"},
    "evidence": {"kind", "summary", "detail", "source_refs"},
}


def parse_episode_item(value: dict[str, Any]) -> DraftEpisodeItem:
    kind = value.get("kind")
    if kind not in _ITEM_KEYS:
        raise ValueError(f"unknown episode item kind {kind!r}")
    actual = set(value)
    expected = _ITEM_KEYS[kind]
    if actual != expected:
        raise ValueError(
            f"{kind} keys must be exactly {sorted(expected)!r}; got {sorted(actual)!r}"
        )
    refs = _string_tuple(value["source_refs"], field="source_refs")
    if kind == "outcome":
        return DraftOutcome(
            _string(value["summary"], field="summary"),
            _string(value["detail"], field="detail"),
            refs,
        )
    if kind == "decision":
        return DraftDecision(
            _string(value["summary"], field="summary"),
            _string(value["reason"], field="reason"),
            _string(value["status"], field="status"),
            refs,
        )
    if kind == "correction":
        return DraftCorrection(
            _string(value["before"], field="before"),
            _string(value["after"], field="after"),
            _string(value["reason"], field="reason"),
            refs,
        )
    if kind == "open_loop":
        return DraftOpenLoop(
            _string(value["summary"], field="summary"),
            _string(value["reason"], field="reason"),
            _string(value["status"], field="status"),
            refs,
        )
    return DraftEvidence(
        _string(value["summary"], field="summary"),
        _string(value["detail"], field="detail"),
        refs,
    )


def episode_item_to_dict(item: DraftEpisodeItem) -> dict[str, Any]:
    payload = asdict(item)
    payload["source_refs"] = list(item.source_refs)
    payload["kind"] = item.kind
    return payload


def episode_item_text(item: DraftEpisodeItem) -> str:
    if isinstance(item, DraftCorrection):
        text = f"原来以为 {item.before}，现确认 {item.after}"
        return _with_suffix(text, "依据", item.reason)
    if isinstance(item, DraftDecision):
        return _with_suffix(item.summary, "理由", item.reason)
    if isinstance(item, DraftOpenLoop):
        return _with_suffix(item.summary, "原因", item.reason)
    return _join_detail(item.summary, item.detail)


def project_episode_items(
    items: tuple[DraftEpisodeItem, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    outcomes: list[str] = []
    decisions: list[str] = []
    corrections: list[str] = []
    open_loops: list[str] = []
    for item in items:
        text = episode_item_text(item)
        if isinstance(item, DraftOutcome):
            outcomes.append(text)
        elif isinstance(item, DraftDecision) and item.status == "active":
            decisions.append(text)
        elif isinstance(item, DraftCorrection):
            corrections.append(text)
        elif isinstance(item, DraftOpenLoop) and item.status == "active":
            open_loops.append(text)
    return tuple(outcomes), tuple(decisions), tuple(corrections), tuple(open_loops)


def _string(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"episode item {field} must be a string")
    return value.strip()


def _string_tuple(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TypeError(f"episode item {field} must be a list of strings")
    return tuple(item.strip() for item in value)


def _join_detail(summary: str, detail: str) -> str:
    return f"{summary}；{detail}" if detail else summary


def _with_suffix(text: str, label: str, value: str) -> str:
    return f"{text}（{label}：{value}）" if value else text
