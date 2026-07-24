"""可序列化、可覆盖的晋升门禁与指标可信度策略。

候选记录生效策略，指标同时报告覆盖率和样本量；所有调用方共用
``quality_label``，避免相同证据得到不同可信度标签。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

QualityLabel = Literal["reliable", "partial", "insufficient"]

# 默认策略形态变化时同步更新，使候选可以追溯生成时的策略。
_POLICY_VERSION = "slice-065-2026-07-18"

_TUPLE_FIELDS = frozenset({"allowed_kinds", "allowed_verification"})
_INTEGER_FIELDS = frozenset(
    {
        "min_helpful_sessions",
        "max_harmful_sessions",
        "min_distinct_days",
        "min_identity_sample_reliable",
        "min_judgement_sample_reliable",
    }
)
_COVERAGE_FIELDS = frozenset(
    {"min_identity_coverage_reliable", "min_judgement_coverage_reliable"}
)


@dataclass(frozen=True)
class PromotionPolicy:
    """晋升门禁及身份、判断覆盖率的可信度阈值。

    晋升证据按独立用户会话计数，并须跨越足够日期；未测试推断不能进入允许的
    verification 集合。
    """

    version: str = _POLICY_VERSION
    allowed_kinds: tuple[str, ...] = ("gotcha", "procedure")
    allowed_verification: tuple[str, ...] = ("verified", "event-data-supported")
    min_helpful_sessions: int = 3
    max_harmful_sessions: int = 0
    min_distinct_days: int = 2
    min_identity_coverage_reliable: float = 0.8
    min_identity_sample_reliable: int = 20
    min_judgement_coverage_reliable: float = 0.5
    min_judgement_sample_reliable: int = 5

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # tuple 字段落盘为 list，保持 JSON shape 稳定。
        d["allowed_kinds"] = list(self.allowed_kinds)
        d["allowed_verification"] = list(self.allowed_verification)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "PromotionPolicy":
        """在默认值上应用合法的 partial override；忽略未知键。"""
        base = cls()
        if d is None:
            return base
        if not isinstance(d, dict):
            raise ValueError("promotion policy must be a JSON object")
        if not d:
            return base
        names = {f.name for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        for key, val in d.items():
            if key not in names:
                logger.debug("ignoring unknown policy key %r", key)
                continue
            if key == "version":
                if not isinstance(val, str):
                    raise ValueError("promotion policy version must be a string")
            elif key in _TUPLE_FIELDS:
                if not isinstance(val, (list, tuple)) or not all(
                    isinstance(item, str) for item in val
                ):
                    raise ValueError(f"promotion policy {key} must be a string array")
                val = tuple(val)
                if key == "allowed_verification" and "inferred-untested" in val:
                    raise ValueError(
                        "inferred-untested must never be allowed_verification (C-7)"
                    )
            elif key in _INTEGER_FIELDS:
                if isinstance(val, bool) or not isinstance(val, int):
                    raise ValueError(f"promotion policy {key} must be an integer")
            elif key in _COVERAGE_FIELDS and (
                isinstance(val, bool) or not isinstance(val, (int, float))
            ):
                raise ValueError(f"promotion policy {key} must be numeric")
            kwargs[key] = val
        return replace(base, **kwargs)

    def identity_quality(self, coverage: float | None, sample: int) -> QualityLabel:
        """按身份覆盖率阈值标记指标可信度。"""
        return quality_label(
            coverage,
            sample,
            min_coverage_reliable=self.min_identity_coverage_reliable,
            min_sample_reliable=self.min_identity_sample_reliable,
        )

    def judgement_quality(self, coverage: float | None, sample: int) -> QualityLabel:
        """按判断覆盖率阈值标记指标可信度。"""
        return quality_label(
            coverage,
            sample,
            min_coverage_reliable=self.min_judgement_coverage_reliable,
            min_sample_reliable=self.min_judgement_sample_reliable,
        )


def quality_label(
    coverage: float | None,
    sample: int,
    *,
    min_coverage_reliable: float,
    min_sample_reliable: int,
) -> QualityLabel:
    """无样本时 insufficient，覆盖率和样本均达标时 reliable，否则 partial。"""
    if sample <= 0:
        return "insufficient"
    if (
        coverage is not None
        and coverage >= min_coverage_reliable
        and sample >= min_sample_reliable
    ):
        return "reliable"
    return "partial"


def default_policy() -> PromotionPolicy:
    """返回默认晋升策略。"""
    return PromotionPolicy()


def load_policy(path: Path | str) -> PromotionPolicy:
    """读取 JSON 策略；文件缺失或内容无效时返回默认策略。"""
    p = Path(path)
    if not p.exists():
        return default_policy()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("promotion policy must be a JSON object")
        return PromotionPolicy.from_dict(raw)
    except (OSError, ValueError) as exc:
        logger.warning("policy %s unreadable (%s); using default", p, exc)
        return default_policy()


def save_policy(policy: PromotionPolicy, path: Path | str) -> None:
    """持久化可重放的 JSON 策略。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(policy.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
