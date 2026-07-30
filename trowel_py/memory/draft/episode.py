"""定义结构化 Episode 草稿事件、严格解析和旧格式投影。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, TypeAlias


@dataclass(frozen=True)
class DraftOutcome:
    """一条已经产生结果的事件。

    Attributes:
        summary: 结果摘要。
        detail: 可选的补充说明；空字符串表示没有补充。
        kind: 固定为 ``"outcome"`` 的事件种类。
    """

    summary: str
    detail: str
    kind: Literal["outcome"] = field(default="outcome", init=False)


@dataclass(frozen=True)
class DraftDecision:
    """一条带理由和生命周期状态的决策。

    Attributes:
        summary: 决策摘要。
        reason: 作出该决策的理由。
        status: 决策状态；整稿校验只接受 ``active`` 或 ``superseded``。
        kind: 固定为 ``"decision"`` 的事件种类。
    """

    summary: str
    reason: str
    status: str
    kind: Literal["decision"] = field(default="decision", init=False)


@dataclass(frozen=True)
class DraftCorrection:
    """一条从原认识改为新结论的更正。

    Attributes:
        before: 被更正的原认识。
        after: 更正后的结论。
        reason: 更正依据。
        kind: 固定为 ``"correction"`` 的事件种类。
    """

    before: str
    after: str
    reason: str
    kind: Literal["correction"] = field(default="correction", init=False)


@dataclass(frozen=True)
class DraftOpenLoop:
    """一条尚未完成或已经关闭的待续事项。

    Attributes:
        summary: 待续事项摘要。
        reason: 该事项仍需跟进或被关闭的原因。
        status: 事项状态；整稿校验只接受 ``active`` 或 ``closed``。
        kind: 固定为 ``"open_loop"`` 的事件种类。
    """

    summary: str
    reason: str
    status: str
    kind: Literal["open_loop"] = field(default="open_loop", init=False)


@dataclass(frozen=True)
class DraftEvidence:
    """一条只记录观察事实、不表达结论的证据。

    Attributes:
        summary: 观察摘要。
        detail: 可选的事实明细；空字符串表示没有明细。
        kind: 固定为 ``"evidence"`` 的事件种类。
    """

    summary: str
    detail: str
    kind: Literal["evidence"] = field(default="evidence", init=False)


DraftEpisodeItem: TypeAlias = (
    DraftOutcome | DraftDecision | DraftCorrection | DraftOpenLoop | DraftEvidence
)

_ITEM_KEYS: dict[str, set[str]] = {
    "outcome": {"kind", "summary", "detail"},
    "decision": {"kind", "summary", "reason", "status"},
    "correction": {"kind", "before", "after", "reason"},
    "open_loop": {"kind", "summary", "reason", "status"},
    "evidence": {"kind", "summary", "detail"},
}


def parse_episode_item(value: dict[str, Any]) -> DraftEpisodeItem:
    """按事件种类严格解析一个字典。

    这里只接受各事件种类规定的完整字段集合，并清理所有字符串首尾空白。
    空字段和非法状态留给整稿校验处理。

    Args:
        value: 待解析的事件字典。

    Returns:
        与 ``kind`` 对应的不可变事件对象。

    Raises:
        ValueError: ``kind`` 未知，或字段集合不完全匹配。
        TypeError: ``kind`` 无法作为事件种类判断，或文本字段不是字符串。
    """
    kind = value.get("kind")
    if kind not in _ITEM_KEYS:
        raise ValueError(f"unknown episode item kind {kind!r}")
    actual = set(value)
    expected = _ITEM_KEYS[kind]
    if actual != expected:
        raise ValueError(
            f"{kind} keys must be exactly {sorted(expected)!r}; got {sorted(actual)!r}"
        )
    if kind == "outcome":
        return DraftOutcome(
            _string(value["summary"], field="summary"),
            _string(value["detail"], field="detail"),
        )
    if kind == "decision":
        return DraftDecision(
            _string(value["summary"], field="summary"),
            _string(value["reason"], field="reason"),
            _string(value["status"], field="status"),
        )
    if kind == "correction":
        return DraftCorrection(
            _string(value["before"], field="before"),
            _string(value["after"], field="after"),
            _string(value["reason"], field="reason"),
        )
    if kind == "open_loop":
        return DraftOpenLoop(
            _string(value["summary"], field="summary"),
            _string(value["reason"], field="reason"),
            _string(value["status"], field="status"),
        )
    return DraftEvidence(
        _string(value["summary"], field="summary"),
        _string(value["detail"], field="detail"),
    )


def episode_item_to_dict(item: DraftEpisodeItem) -> dict[str, Any]:
    """把事件转换成可直接进行 JSON 编码的字典。"""
    payload = asdict(item)
    payload["kind"] = item.kind
    return payload


def episode_item_text(item: DraftEpisodeItem) -> str:
    """生成兼容投影和 Episode 渲染使用的单条可读文本。

    更正会同时呈现新旧认识；决策和待续事项会在理由非空时附加带标签的
    后缀；结果和证据则用分号连接摘要与非空明细。状态和来源引用不进入文本。
    """
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
    """把结构化事件投影为旧调用方使用的四组文本。

    返回值依次为结果、有效决策、更正和有效待续事项，并在各组内保留输入
    顺序。证据、已替代的决策和已关闭的待续事项不会进入旧格式。
    """
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
    """要求字段为字符串并移除首尾空白，允许得到空字符串。"""
    if not isinstance(value, str):
        raise TypeError(f"episode item {field} must be a string")
    return value.strip()


def _join_detail(summary: str, detail: str) -> str:
    """有明细时用中文分号连接摘要，否则只返回摘要。"""
    return f"{summary}；{detail}" if detail else summary


def _with_suffix(text: str, label: str, value: str) -> str:
    """说明非空时附加中文括号标签，否则原样返回正文。"""
    return f"{text}（{label}：{value}）" if value else text
