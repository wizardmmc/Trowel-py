from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass

from trowel_py.memory.profile_suggestions import PROFILE_DISTILL_POLICY_VERSION
from trowel_py.memory.types import Suggestion

logger = logging.getLogger("trowel_py.memory.profile_distill_job")

_VALID_DIMS: frozenset[str] = frozenset(
    {"ability", "methodology", "expression", "goal", "other"}
)
_PROFILE_BODY_MAX_CHARS = 60
_PROFILE_SUGGESTIONS_MAX_PER_SEGMENT = 2


class DistillError(Exception):
    """当前 session 无法完成画像提炼。"""


@dataclass(frozen=True)
class GateStats:
    raw: int = 0
    accepted: int = 0
    dropped_empty_body: int = 0
    dropped_too_long: int = 0
    dropped_no_evidence: int = 0
    over_limit: int = 0

    def to_log_dict(self) -> dict[str, int]:
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
    accepted: tuple[Suggestion, ...]
    stats: GateStats


def _stamp_sources(sources: object, cc_session_id: str) -> tuple[str, ...]:
    """为来源补充 session id；非 list 输入按无证据处理。"""
    if isinstance(sources, list):
        out = [str(s) for s in sources]
    else:
        if sources:
            logger.debug(
                "distill: suggestion sources not a list, dropping: %r", sources
            )
        out = []
    if cc_session_id and cc_session_id not in out:
        out = [cc_session_id, *out]
    return tuple(out)


def _has_evidence(sources: tuple[str, ...], cc_session_id: str) -> bool:
    """Session id 只用于溯源，不能单独充当用户证据。"""
    for s in sources:
        cleaned = s.strip()
        if cleaned and cleaned != cc_session_id:
            return True
    return False


def parse_and_gate_draft(
    text: str,
    *,
    cc_session_id: str,
    date_str: str,
    policy_version: int = PROFILE_DISTILL_POLICY_VERSION,
) -> GatedDraft:
    """解析 draft 并执行门禁；结构合法但全部丢弃时仍返回空结果。"""
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
            raise DistillError(
                f"unknown dimension {dim!r} in suggestions-draft.json"
            )
        body = str(item.get("body") or "")
        if not body.strip():
            dropped_empty_body += 1
            continue
        if len(body) > _PROFILE_BODY_MAX_CHARS:
            dropped_too_long += 1
            continue
        sources = _stamp_sources(item.get("sources", []), cc_session_id)
        if not _has_evidence(sources, cc_session_id):
            dropped_no_evidence += 1
            continue
        accepted.append(
            Suggestion(
                id=uuid.uuid4().hex,
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
    # raw 只统计维度合法的 dict；未知维度会让整个 draft 失败。
    raw = (
        dropped_empty_body + dropped_too_long + dropped_no_evidence + len(accepted)
    )
    stats = GateStats(
        raw=raw,
        accepted=len(kept),
        dropped_empty_body=dropped_empty_body,
        dropped_too_long=dropped_too_long,
        dropped_no_evidence=dropped_no_evidence,
        over_limit=over_limit,
    )
    return GatedDraft(accepted=kept, stats=stats)
