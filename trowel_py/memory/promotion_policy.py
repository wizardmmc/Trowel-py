"""定义可序列化、可局部覆盖的晋升门禁与指标可信度策略。

候选记录生效策略及其内容摘要；身份和判断指标共用同一套覆盖率、样本量分级
规则。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

QualityLabel = Literal["reliable", "partial", "insufficient"]

# 默认字段或阈值变化时须同步更新；候选会记录该版本用于追溯。
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

    晋升证据按独立用户会话计数，并须跨越足够日期。默认策略和 ``from_dict``
    禁止把未测试推断放入 verification 集合；直接构造不保证该限制，也不校验
    类型、正负范围、比例区间或有限性。

    Attributes:
        version: 写入候选的策略版本文本。
        allowed_kinds: 允许生成 Core 候选的笔记类型。
        allowed_verification: 允许晋升的验证状态；字典加载禁止包含
            ``inferred-untested``。
        min_helpful_sessions: 晋升所需的最少帮助会话数。
        max_harmful_sessions: 晋升允许的最多有害会话数。
        min_distinct_days: 晋升证据所需的最少不同日期数。
        min_identity_coverage_reliable: 身份指标评为 reliable 的覆盖率下限。
        min_identity_sample_reliable: 身份指标评为 reliable 的样本量下限。
        min_judgement_coverage_reliable: 判断指标评为 reliable 的覆盖率下限。
        min_judgement_sample_reliable: 判断指标评为 reliable 的样本量下限。
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
        """深转换全部策略字段，并规范化两个 tuple 字段。

        Returns:
            保持 dataclass 字段顺序的深转换字典；两个允许集合显式转换为新
            列表。
        """
        d = asdict(self)
        # 允许集合统一写成 JSON 数组，不暴露 dataclass 的 tuple 表示。
        d["allowed_kinds"] = list(self.allowed_kinds)
        d["allowed_verification"] = list(self.allowed_verification)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "PromotionPolicy":
        """在默认策略上应用通过类型检查的局部覆盖。

        ``None`` 或空字典返回新的 ``cls`` 实例，未知键只记录 debug 日志。
        本类内置的 10 个字段按下述规则检查；dataclass 子类新增的已知字段不会
        经过这些分支，而会直接交给 ``replace``。允许集合
        接受字符串 list/tuple 并规范化为 tuple；整数阈值拒绝 bool，覆盖率
        阈值接受 int/float 但拒绝 bool。函数不检查负数、覆盖率区间、NaN、
        infinity 或空集合。

        Args:
            d: 策略字段字典；``None`` 表示没有覆盖。

        Returns:
            基于 ``cls()`` 默认值替换已知字段后的同类型新实例。

        Raises:
            ValueError: 顶层不是字典，内置字段类型非法，或允许的验证状态包含
                ``inferred-untested``。
        """
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
        """使用本策略的身份阈值计算指标可信度。

        Args:
            coverage: 身份覆盖率；``None`` 表示没有覆盖率。
            sample: 身份样本量。

        Returns:
            ``quality_label`` 的三档结果。
        """
        return quality_label(
            coverage,
            sample,
            min_coverage_reliable=self.min_identity_coverage_reliable,
            min_sample_reliable=self.min_identity_sample_reliable,
        )

    def judgement_quality(self, coverage: float | None, sample: int) -> QualityLabel:
        """使用本策略的判断阈值计算指标可信度。

        Args:
            coverage: 判断覆盖率；``None`` 表示没有覆盖率。
            sample: 判断样本量。

        Returns:
            ``quality_label`` 的三档结果。
        """
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
    """按样本量和覆盖率阈值返回三档可信度。

    ``sample <= 0`` 时始终为 insufficient。正样本下，coverage 非 ``None`` 且
    覆盖率和样本量都不低于阈值时为 reliable，否则为 partial。函数不校验
    数值范围；NaN 覆盖率因比较不成立而得到 partial。

    Args:
        coverage: 可选覆盖率。
        sample: 样本量。
        min_coverage_reliable: reliable 所需覆盖率下限。
        min_sample_reliable: reliable 所需样本量下限。

    Returns:
        ``reliable``、``partial`` 或 ``insufficient``。
    """
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
    """返回一个新的默认晋升策略实例。"""
    return PromotionPolicy()


def load_policy(path: Path | str) -> PromotionPolicy:
    """读取 JSON 策略，常见文件或内容错误时回退默认值。

    路径缺失时静默返回默认策略。文件读取、UTF-8/JSON 解析或已知字段校验
    失败时记录告警并返回默认策略；``Path`` 构造和存在性检查位于捕获范围
    之外。

    Args:
        path: JSON 策略文件路径或路径文本；不展开 ``~``。

    Returns:
        文件中的局部覆盖策略，或新的默认策略。
    """
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
    """以稳定 JSON 格式覆盖保存完整策略。

    函数创建父目录，以两空格缩进并保留非 ASCII 字符，不追加末尾换行。写入
    不使用临时文件原子替换。非有限浮点数沿用 Python JSON 编码器的
    ``NaN``/``Infinity`` 表示，不保证严格 JSON 兼容。

    Args:
        policy: 要完整持久化的策略。
        path: 目标文件路径或路径文本；不展开 ``~``。

    Raises:
        OSError: 无法创建父目录或写入策略文件。
        UnicodeEncodeError: 策略文本含 UTF-8 无法编码的字符。
        TypeError: 路径或策略字段无法转换、序列化。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(policy.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
