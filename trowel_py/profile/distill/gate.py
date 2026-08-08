"""解析 Profile 建议草稿，并执行数量、长度和证据门禁。"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass

from trowel_py.profile.distill.models import EvidenceValidator
from trowel_py.profile.models import Suggestion
from trowel_py.profile.suggestions import PROFILE_DISTILL_POLICY_VERSION

logger = logging.getLogger(__name__)

_VALID_DIMS: frozenset[str] = frozenset(
    {"ability", "methodology", "expression", "goal", "other"}
)
_PROFILE_BODY_MAX_CHARS = 60
_PROFILE_SUGGESTIONS_MAX_PER_SEGMENT = 2


class DistillError(Exception):
    """表示当前会话无法完成 Profile 建议提炼。"""


@dataclass(frozen=True)
class GateStats:
    """一次建议草稿门禁的保留、丢弃和超量统计。

    三类 ``dropped_*`` 按正文为空、正文过长、没有证据的顺序只计入首个失败
    原因，彼此互斥。``over_limit`` 只统计已通过这三道门禁但超过数量上限的项。

    Attributes:
        raw: 维度合法的对象项总数，包括门禁丢弃项和超过数量上限的项；非对象项
            不计入，未知维度会让整份草稿失败而不返回统计。
        accepted: 最终保留的建议数，最多为 2。
        dropped_empty_body: 正文转换后为空或只有空白的项数。
        dropped_too_long: 通过空正文门禁后，长度超过 60 个字符的项数。
        dropped_no_evidence: 通过两道正文门禁后，没有来源身份之外非空来源证据
            的项数。
        over_limit: 通过其余门禁但排在前两项之后的建议数。
    """

    raw: int = 0
    accepted: int = 0
    dropped_empty_body: int = 0
    dropped_too_long: int = 0
    dropped_no_evidence: int = 0
    over_limit: int = 0

    def to_log_dict(self) -> dict[str, int]:
        """返回包含全部六项计数的新日志字典。"""
        return {
            "raw": self.raw,
            "accepted": self.accepted,
            "dropped_empty_body": self.dropped_empty_body,
            "dropped_too_long": self.dropped_too_long,
            "dropped_no_evidence": self.dropped_no_evidence,
            "over_limit": self.over_limit,
        }


@dataclass(frozen=True)
class GatedDraft:
    """汇总通过门禁的建议及本次统计。

    Attributes:
        accepted: 按草稿顺序保留的前两条合格建议。
        stats: 整份草稿的门禁统计。
    """

    accepted: tuple[Suggestion, ...]
    stats: GateStats


def _stamp_sources(sources: object, source_id: str) -> tuple[str, ...]:
    """规范化来源列表，并在需要时前置稳定来源身份。

    list 中每一项直接转换为字符串，不去重、不裁剪空白，并保留顺序。非 list
    输入整体丢弃；非空的错误输入会记录 debug 日志。来源身份非空且尚未原样
    出现时才前置。

    Args:
        sources: 草稿中的来源值。
        source_id: 用于溯源的运行时无关来源身份。

    Returns:
        规范化并补充来源身份的来源元组。
    """
    if isinstance(sources, list):
        out = [str(s) for s in sources]
    else:
        if sources:
            logger.debug(
                "distill: suggestion sources not a list, dropping: %r", sources
            )
        out = []
    if source_id and source_id not in out:
        out = [source_id, *out]
    return tuple(out)


def _filter_target_evidence(
    sources: tuple[str, ...],
    source_id: str,
    evidence_validator: EvidenceValidator | None,
) -> tuple[str, ...]:
    """只保留来源身份和通过当前 target 校验的证据。

    没有校验器时原样返回，保持 Claude Code 现有行为。提供校验器时，来源
    身份始终保留；其他项先去掉首尾空白再交给校验器，不通过的 context 文本
    不会进入最终建议。

    Args:
        sources: 已由 ``_stamp_sources`` 规范化的来源。
        source_id: 始终保留的稳定来源身份。
        evidence_validator: 可选的 target 证据校验函数。

    Returns:
        只包含来源身份和合格 target 证据的元组。
    """
    if evidence_validator is None:
        return sources
    return tuple(
        source
        for source in sources
        if source == source_id
        or (source.strip() and evidence_validator(source.strip()))
    )


def _has_evidence(sources: tuple[str, ...], source_id: str) -> bool:
    """判断来源中是否有稳定身份之外的非空证据。

    Args:
        sources: 已规范化并按 target 规则过滤的来源。
        source_id: 要排除的稳定来源身份。

    Returns:
        是否至少存在一项非空的非身份来源。
    """
    for s in sources:
        cleaned = s.strip()
        if cleaned and cleaned != source_id:
            return True
    return False


def parse_and_gate_draft(
    text: str,
    *,
    source_id: str,
    date_str: str,
    evidence_validator: EvidenceValidator | None = None,
    policy_version: int = PROFILE_DISTILL_POLICY_VERSION,
) -> GatedDraft:
    """解析 Profile 建议草稿，并执行结构、正文、证据和数量门禁。

    顶层必须是对象，``suggestions`` 缺失时按空列表处理，存在时必须是列表。
    列表中的非对象项会跳过且不计入统计；任一对象使用未知维度会让整份草稿
    失败。正文的假值按空文本处理，其他值转为字符串；只用 ``strip()`` 判断
    是否为空，长度和最终保存均使用未裁剪文本。正文最多 60 个字符。

    来源必须是 list，且除自动补入的来源身份外至少有一项非空证据；提供
    ``evidence_validator`` 时，这项证据还必须通过当前 target 的归属校验。
    合格建议按原顺序最多保留两条，并生成 UUID、``pending`` 状态、调用方
    日期和策略版本；草稿中的其他字段会忽略。不做建议间去重。结构合法但
    所有项被丢弃时返回空 ``accepted``，不会报错。

    Args:
        text: ``suggestions-draft.json`` 的完整文本。
        source_id: 写入每条合格建议来源的稳定身份；仅非空时补入，且应不含
            首尾空白。
        date_str: 写入每条合格建议的日期文本。
        evidence_validator: 可选的 target 证据归属校验函数；不提供时接受任意
            非空的非身份来源。
        policy_version: 写入每条合格建议的策略版本。

    Returns:
        最多两条合格建议及整份草稿的门禁统计。

    Raises:
        DistillError: JSON、顶层、``suggestions`` 结构无效，或存在未知维度。
        TypeError: ``dimension`` 是无法用于集合成员判断的值。
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DistillError(f"suggestions-draft.json is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DistillError("suggestions-draft.json top level is not an object")
    raw_list = data.get("suggestions", [])
    if not isinstance(raw_list, list):
        raise DistillError("suggestions-draft.json 'suggestions' is not a list")

    accepted: list[Suggestion] = []
    dropped_empty_body = 0
    dropped_too_long = 0
    dropped_no_evidence = 0
    for item in raw_list:
        if not isinstance(item, dict):
            logger.debug("distill: skipping non-dict suggestion item: %r", item)
            continue
        dim = item.get("dimension")
        if dim not in _VALID_DIMS:
            raise DistillError(f"unknown dimension {dim!r} in suggestions-draft.json")
        body = str(item.get("body") or "")
        if not body.strip():
            dropped_empty_body += 1
            continue
        if len(body) > _PROFILE_BODY_MAX_CHARS:
            dropped_too_long += 1
            continue
        sources = _stamp_sources(item.get("sources", []), source_id)
        sources = _filter_target_evidence(
            sources,
            source_id,
            evidence_validator,
        )
        if not _has_evidence(sources, source_id):
            dropped_no_evidence += 1
            continue
        # 维度已通过 _VALID_DIMS 门禁，但类型检查器不会据此收窄 Literal。
        # pending 是 SuggestionStatus 的合法常量；两处忽略都只弥合静态类型。
        suggestion_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            json.dumps(
                {
                    "source_id": source_id,
                    "dimension": dim,
                    "body": body,
                    "sources": sources,
                    "policy_version": policy_version,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        ).hex
        accepted.append(
            Suggestion(
                id=suggestion_id,
                dimension=dim,  # type: ignore[arg-type]
                body=body,
                sources=sources,
                date=date_str,
                status="pending",  # type: ignore[arg-type]
                policy_version=policy_version,
            )
        )

    over_limit = max(0, len(accepted) - _PROFILE_SUGGESTIONS_MAX_PER_SEGMENT)
    kept = tuple(accepted[:_PROFILE_SUGGESTIONS_MAX_PER_SEGMENT])
    # raw 排除非对象项；未知维度会让整份草稿失败，因而没有可返回的统计。
    raw = dropped_empty_body + dropped_too_long + dropped_no_evidence + len(accepted)
    stats = GateStats(
        raw=raw,
        accepted=len(kept),
        dropped_empty_body=dropped_empty_body,
        dropped_too_long=dropped_too_long,
        dropped_no_evidence=dropped_no_evidence,
        over_limit=over_limit,
    )
    return GatedDraft(accepted=kept, stats=stats)
