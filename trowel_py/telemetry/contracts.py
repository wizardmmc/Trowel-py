"""校验版本化遥测批次，并在入库前移除不受控输入。"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from trowel_py.telemetry.catalog import (
    COMPONENTS,
    METRIC_KINDS,
    METRIC_NAMES,
    METRIC_UNITS,
    MODEL_NAME_MAX_LENGTH,
    OPERATIONS,
    RUNTIMES,
    STATUSES,
)

SCHEMA_VERSION = 1
NORMAL_BATCH_LIMIT = 250
BACKFILL_BATCH_LIMIT = 1_000

_BATCH_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_METRIC_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_HEX_16_PATTERN = re.compile(r"^[0-9a-fA-F]{16}$")
_HEX_32_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_PRIVATE_FIELD_NAMES = frozenset(
    {
        "prompt",
        "thinking",
        "tool_args",
        "tool_arguments",
        "tool_result",
        "tool_results",
        "sql",
        "credential",
        "credentials",
        "password",
        "api_key",
        "path",
        "absolute_path",
        "cwd",
        "workdir",
    }
)


class TelemetryAttributes(BaseModel):
    """保存 schema v1 允许进入遥测的低基数字段。

    Attributes:
        quality: 产生该事实的来源质量。
        sampled: 上报方是否对同类事实做过采样。
        retry_count: 当前受控操作已经重试的次数。
        row_count_bucket: 数据量的区间标签，不记录精确高基数值。
        transport: 调用经过的受控传输类型。
        error_category: 不包含异常正文的错误分类。
        black_box: 是否存在 native runtime 不可见区间。
        exit_mode: Host 是否在 cooperative 阶段完成退出。
        process_tree_result: Host 最终核验的进程树终态。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    quality: Literal["reliable", "partial", "unavailable"] | None = None
    sampled: bool | None = None
    retry_count: int | None = Field(default=None, ge=0, le=100)
    row_count_bucket: Literal["0", "1-10", "11-100", "101-1000", "1000+"] | None = None
    transport: Literal["http", "sse", "stdio", "ipc", "sqlite"] | None = None
    error_category: Literal[
        "timeout",
        "busy",
        "locked",
        "validation",
        "unavailable",
        "cancelled",
        "crash",
        "unknown",
    ] | None = None
    black_box: bool | None = None
    exit_mode: Literal["cooperative", "forced"] | None = None
    process_tree_result: Literal["closed", "needs_reconcile"] | None = None


class TraceLinkInput(BaseModel):
    """表示当前 span 与另一条可核查 trace/span 的非父子关联。

    Attributes:
        trace_id: 关联 trace 的 16 字节十六进制身份。
        span_id: 已知时记录关联 span 的 8 字节十六进制身份。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_id: str
    span_id: str | None = None

    @field_validator("trace_id")
    @classmethod
    def validate_trace_id(cls, value: str) -> str:
        """拒绝不是 16 字节十六进制的 trace identity。"""

        if not _HEX_32_PATTERN.fullmatch(value):
            raise ValueError("trace_id must be 16-byte hex")
        return value.lower()

    @field_validator("span_id")
    @classmethod
    def validate_optional_span_id(cls, value: str | None) -> str | None:
        """拒绝不是 8 字节十六进制的可选 span identity。"""

        if value is not None and not _HEX_16_PATTERN.fullmatch(value):
            raise ValueError("span_id must be 8-byte hex")
        return value.lower() if value is not None else None


class TelemetrySpanInput(BaseModel):
    """描述一次受控操作的开始、结束、归属和本机关联。

    Attributes:
        trace_id: 一条调用链共用的 16 字节十六进制身份。
        span_id: 当前操作的 8 字节十六进制身份。
        parent_span_id: 已传播父上下文时的父 span 身份；黑盒关联不得填入。
        started_at: 操作开始的带时区时间。
        ended_at: 操作结束的带时区时间，不得早于开始时间。
        component: 实际执行该操作的受控组件。
        operation: 不含动态路径或参数的受控操作名。
        status: 操作成功、失败或没有终态的分类。
        runtime: 仅 runtime 相关操作填写 Claude Code 或 Codex。
        model: 仅模型相关操作填写 runtime 实际回报的模型名。
        session_ref: 只供本机下钻的不透明引用，入库时转换为固定宽度摘要。
        call_ref: 只供本机下钻的调用引用，入库时转换为固定宽度摘要。
        attributes: schema v1 允许的低基数字段。
        links: 当前操作与其他 trace/span 的可核查非父子关联。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    started_at: datetime
    ended_at: datetime
    component: str
    operation: str
    status: str
    runtime: str | None = None
    model: str | None = Field(default=None, max_length=MODEL_NAME_MAX_LENGTH)
    session_ref: str | None = Field(default=None, max_length=256)
    call_ref: str | None = Field(default=None, max_length=256)
    attributes: TelemetryAttributes = Field(default_factory=TelemetryAttributes)
    links: list[TraceLinkInput] = Field(default_factory=list, max_length=16)

    @field_validator("trace_id")
    @classmethod
    def validate_trace_id(cls, value: str) -> str:
        """校验并规范化 16 字节 trace identity。"""

        if not _HEX_32_PATTERN.fullmatch(value):
            raise ValueError("trace_id must be 16-byte hex")
        return value.lower()

    @field_validator("span_id", "parent_span_id")
    @classmethod
    def validate_span_id(cls, value: str | None) -> str | None:
        """校验并规范化 8 字节 span identity。"""

        if value is not None and not _HEX_16_PATTERN.fullmatch(value):
            raise ValueError("span identity must be 8-byte hex")
        return value.lower() if value is not None else None

    @field_validator("started_at", "ended_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """拒绝无法确定真实时刻的无时区时间。"""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include timezone")
        return value

    @field_validator("component")
    @classmethod
    def validate_component(cls, value: str) -> str:
        """只接受集中目录中的组件。"""

        if value not in COMPONENTS:
            raise ValueError("unsupported component")
        return value

    @field_validator("operation")
    @classmethod
    def validate_operation(cls, value: str) -> str:
        """只接受集中目录中的操作名。"""

        if value not in OPERATIONS:
            raise ValueError("unsupported operation")
        return value

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        """只接受稳定的操作终态分类。"""

        if value not in STATUSES:
            raise ValueError("unsupported status")
        return value

    @field_validator("runtime")
    @classmethod
    def validate_runtime(cls, value: str | None) -> str | None:
        """只接受两种原生 runtime。"""

        if value is not None and value not in RUNTIMES:
            raise ValueError("unsupported runtime")
        return value

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str | None) -> str | None:
        """接受 runtime 实际模型名，并拒绝无法安全聚合的字符串。"""

        return _validate_model_name(value)

    @model_validator(mode="after")
    def validate_interval_and_model(self) -> "TelemetrySpanInput":
        """保证耗时非负、有限且模型不会脱离 runtime 出现。"""

        duration = (self.ended_at - self.started_at).total_seconds()
        if duration < 0 or duration > 86_400:
            raise ValueError("span interval is invalid")
        if self.model is not None and self.runtime is None:
            raise ValueError("model requires runtime")
        return self


class TelemetryMetricInput(BaseModel):
    """描述一个带受控维度的运行时数值样本。

    Attributes:
        metric_id: 上报方在批次内稳定生成的幂等身份。
        observed_at: 采样时刻。
        component: 产生样本的受控组件。
        name: schema v1 指标目录中的名称。
        kind: counter、gauge 或 histogram。
        unit: 次数、字节或毫秒单位。
        value: 有限数值；当前公共指标只接受非负值。
        status: 样本关联操作的受控终态。
        runtime: 可选原生 runtime 维度。
        model: 可选且由 runtime 实际回报的模型维度。
        operation: 可选受控操作维度。
        attributes: schema v1 允许的低基数字段。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_id: str
    observed_at: datetime
    component: str
    name: str
    kind: str
    unit: str
    value: float
    status: str
    runtime: str | None = None
    model: str | None = Field(default=None, max_length=MODEL_NAME_MAX_LENGTH)
    operation: str | None = None
    attributes: TelemetryAttributes = Field(default_factory=TelemetryAttributes)

    @field_validator("metric_id")
    @classmethod
    def validate_metric_id(cls, value: str) -> str:
        """拒绝可能携带正文或路径的 metric identity。"""

        if not _METRIC_ID_PATTERN.fullmatch(value):
            raise ValueError("invalid metric_id")
        return value

    @field_validator("observed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """拒绝无时区采样时间。"""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include timezone")
        return value

    @field_validator("component")
    @classmethod
    def validate_component(cls, value: str) -> str:
        """只接受集中目录中的组件。"""

        if value not in COMPONENTS:
            raise ValueError("unsupported component")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """只接受 schema v1 指标目录中的名称。"""

        if value not in METRIC_NAMES:
            raise ValueError("unsupported metric")
        return value

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        """只接受可稳定聚合的指标种类。"""

        if value not in METRIC_KINDS:
            raise ValueError("unsupported metric kind")
        return value

    @field_validator("unit")
    @classmethod
    def validate_unit(cls, value: str) -> str:
        """只接受指标目录已声明的单位。"""

        if value not in METRIC_UNITS:
            raise ValueError("unsupported metric unit")
        return value

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        """只接受稳定的终态分类。"""

        if value not in STATUSES:
            raise ValueError("unsupported status")
        return value

    @field_validator("runtime")
    @classmethod
    def validate_runtime(cls, value: str | None) -> str | None:
        """只接受两种原生 runtime。"""

        if value is not None and value not in RUNTIMES:
            raise ValueError("unsupported runtime")
        return value

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str | None) -> str | None:
        """接受 runtime 实际模型名，并拒绝无法安全聚合的字符串。"""

        return _validate_model_name(value)

    @field_validator("operation")
    @classmethod
    def validate_operation(cls, value: str | None) -> str | None:
        """只接受集中目录中的可选操作名。"""

        if value is not None and value not in OPERATIONS:
            raise ValueError("unsupported operation")
        return value

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: float) -> float:
        """拒绝负数、NaN 和无穷值。"""

        if not isfinite(value) or value < 0:
            raise ValueError("metric value must be finite and non-negative")
        return value

    @model_validator(mode="after")
    def validate_model_runtime(self) -> "TelemetryMetricInput":
        """保证模型维度不会脱离 runtime 出现。"""

        if self.model is not None and self.runtime is None:
            raise ValueError("model requires runtime")
        return self


class TelemetryBatchRequest(BaseModel):
    """承载一个来源组件提交的版本化 span/metric 批次。

    Attributes:
        batch_id: 客户端为幂等重试生成的受控身份。
        schema_version: Trowel 遥测公开 schema 版本，当前只接受 1。
        source_component: 发送本批次的组件，不代替每条事实的 component。
        collected_at: 上报方组装批次的带时区时间。
        mode: normal 最多 250 条；backfill 最多 1000 条。
        spans: 等待逐条白名单校验的 span 原始对象。
        metrics: 等待逐条白名单校验的 metric 原始对象。
    """

    model_config = ConfigDict(extra="forbid")

    batch_id: str
    schema_version: int
    source_component: str
    collected_at: datetime
    mode: Literal["normal", "backfill"] = "normal"
    spans: list[dict[str, Any]] = Field(default_factory=list, max_length=1_000)
    metrics: list[dict[str, Any]] = Field(default_factory=list, max_length=1_000)

    @field_validator("batch_id")
    @classmethod
    def validate_batch_id(cls, value: str) -> str:
        """拒绝可能携带路径或正文的批次身份。"""

        if not _BATCH_ID_PATTERN.fullmatch(value):
            raise ValueError("invalid batch_id")
        return value

    @field_validator("collected_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """拒绝无时区批次时间。"""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("collected_at must include timezone")
        return value


@dataclass(frozen=True)
class PreparedTraceLink:
    """保存已转换成固定宽度字节的 span link。

    Attributes:
        trace_id: 关联 trace 的 16 字节身份。
        span_id: 可选关联 span 的 8 字节身份。
    """

    trace_id: bytes
    span_id: bytes | None


@dataclass(frozen=True)
class PreparedSpan:
    """保存可直接写入 telemetry.db 的 span 行。

    Attributes:
        trace_id: 16 字节 trace identity。
        span_id: 8 字节 span identity。
        parent_span_id: 可选的 8 字节父 span identity。
        started_at_ns: UTC Unix epoch 纳秒。
        ended_at_ns: UTC Unix epoch 纳秒。
        duration_ms: 结束减开始得到的毫秒耗时。
        duration_bucket: 可跨小时和日期直接相加的固定耗时桶编号。
        component: 受控组件。
        operation: 受控操作名。
        status: 受控终态。
        runtime: 可选 runtime 维度。
        model: 可选模型维度。
        session_ref: 不透明 session 引用的 16 字节摘要。
        call_ref: 不透明调用引用的 16 字节摘要。
        attributes_json: 只含白名单字段的紧凑 JSON。
        links: 当前 span 的非父子关联。
    """

    trace_id: bytes
    span_id: bytes
    parent_span_id: bytes | None
    started_at_ns: int
    ended_at_ns: int
    duration_ms: float
    duration_bucket: int
    component: str
    operation: str
    status: str
    runtime: str | None
    model: str | None
    session_ref: bytes | None
    call_ref: bytes | None
    attributes_json: str
    links: tuple[PreparedTraceLink, ...]


@dataclass(frozen=True)
class PreparedMetric:
    """保存可直接写入 telemetry.db 的 metric 行。

    Attributes:
        metric_id: 批次内稳定身份。
        observed_at_ns: UTC Unix epoch 纳秒。
        component: 受控组件。
        name: schema v1 指标名。
        kind: 指标聚合种类。
        unit: 指标单位。
        value: 有限非负数值。
        status: 受控终态。
        runtime: 可选 runtime 维度。
        model: 可选模型维度。
        operation: 可选受控操作名。
        attributes_json: 只含白名单字段的紧凑 JSON。
    """

    metric_id: str
    observed_at_ns: int
    component: str
    name: str
    kind: str
    unit: str
    value: float
    status: str
    runtime: str | None
    model: str | None
    operation: str | None
    attributes_json: str


@dataclass(frozen=True)
class PreparedBatch:
    """保存一个已完成逐条校验、可以异步入库的批次。

    Attributes:
        batch_id: 客户端幂等身份。
        fingerprint: 完整请求的 SHA-256；同 ID 不同内容据此拒绝。
        schema_version: Trowel 遥测 schema 版本。
        source_component: 发送批次的受控组件。
        collected_at_ns: 批次采集时刻的 UTC Unix epoch 纳秒。
        mode: normal 或 backfill。
        spans: 已去敏并转换的 span。
        metrics: 已去敏并转换的 metric。
        rejected_count: 本批次未通过校验的记录数。
        error_categories: 拒绝原因及各自记录数。
    """

    batch_id: str
    fingerprint: bytes
    schema_version: int
    source_component: str
    collected_at_ns: int
    mode: str
    spans: tuple[PreparedSpan, ...]
    metrics: tuple[PreparedMetric, ...]
    rejected_count: int
    error_categories: dict[str, int]

    @property
    def accepted_count(self) -> int:
        """返回通过白名单校验的 span 与 metric 总数。"""

        return len(self.spans) + len(self.metrics)


@dataclass(frozen=True)
class TelemetrySubmitResult:
    """描述一次批次提交在 collector 边界得到的确定结果。

    Attributes:
        accepted: 已进入有界内存队列的记录数。
        rejected: 因 schema、版本或隐私规则被拒绝的记录数。
        dropped: 因队列满、关闭或写入失败被丢弃的记录数。
        duplicate: 同一批次是否已经提交过。
        error_categories: 本次拒绝或丢弃的分类计数。
    """

    accepted: int
    rejected: int
    dropped: int
    duplicate: bool
    error_categories: dict[str, int]


def datetime_to_epoch_ns(value: datetime) -> int:
    """把带时区时间精确转换为 UTC Unix epoch 纳秒。"""

    utc = value.astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = utc - epoch
    return (
        delta.days * 86_400_000_000_000
        + delta.seconds * 1_000_000_000
        + delta.microseconds * 1_000
    )


def prepare_batch(request: TelemetryBatchRequest) -> PreparedBatch:
    """逐条校验批次，并只返回可以安全入库的固定形态。

    批次头或大小不受支持时整批拒绝；单条事实失败时保留同批其他合法记录。

    Args:
        request: FastAPI、Python port 或测试构造的版本化批次。

    Returns:
        不再包含原始正文的入库批次和拒绝分类。
    """

    raw = request.model_dump(mode="json")
    fingerprint = hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).digest()
    record_count = len(request.spans) + len(request.metrics)
    batch_error = _batch_error(request, record_count)
    if batch_error is not None:
        return PreparedBatch(
            batch_id=request.batch_id,
            fingerprint=fingerprint,
            schema_version=request.schema_version,
            source_component=request.source_component,
            collected_at_ns=datetime_to_epoch_ns(request.collected_at),
            mode=request.mode,
            spans=(),
            metrics=(),
            rejected_count=record_count,
            error_categories={batch_error: record_count},
        )

    spans: list[PreparedSpan] = []
    metrics: list[PreparedMetric] = []
    errors: Counter[str] = Counter()
    for candidate in request.spans:
        if _contains_private_data(candidate):
            errors["privacy_field"] += 1
            continue
        try:
            spans.append(_prepare_span(TelemetrySpanInput.model_validate(candidate)))
        except ValidationError as exc:
            errors[_validation_category(exc)] += 1
    for candidate in request.metrics:
        if _contains_private_data(candidate):
            errors["privacy_field"] += 1
            continue
        try:
            metrics.append(_prepare_metric(TelemetryMetricInput.model_validate(candidate)))
        except ValidationError as exc:
            errors[_validation_category(exc)] += 1
    return PreparedBatch(
        batch_id=request.batch_id,
        fingerprint=fingerprint,
        schema_version=request.schema_version,
        source_component=request.source_component,
        collected_at_ns=datetime_to_epoch_ns(request.collected_at),
        mode=request.mode,
        spans=tuple(spans),
        metrics=tuple(metrics),
        rejected_count=sum(errors.values()),
        error_categories=dict(sorted(errors.items())),
    )


def _batch_error(request: TelemetryBatchRequest, record_count: int) -> str | None:
    """返回批次头或记录数量的拒绝分类。"""

    if request.schema_version != SCHEMA_VERSION:
        return "unsupported_version"
    if request.source_component not in COMPONENTS:
        return "unsupported_dimension"
    limit = BACKFILL_BATCH_LIMIT if request.mode == "backfill" else NORMAL_BATCH_LIMIT
    if record_count > limit:
        return "batch_too_large"
    return None


def _contains_private_data(value: object) -> bool:
    """递归识别 schema 明令禁止的字段名和绝对路径。"""

    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in _PRIVATE_FIELD_NAMES:
                return True
            if _contains_private_data(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_private_data(item) for item in value)
    if isinstance(value, str):
        return value.startswith("/") or bool(_WINDOWS_ABSOLUTE_PATH.match(value))
    return False


def _validation_category(error: ValidationError) -> str:
    """把 Pydantic 细节收敛为稳定的遥测拒绝类别。"""

    dimension_fields = {
        "component",
        "operation",
        "status",
        "runtime",
        "model",
        "name",
        "kind",
        "unit",
    }
    timestamp_fields = {"started_at", "ended_at", "observed_at"}
    locations = {str(item) for detail in error.errors() for item in detail["loc"]}
    if locations & dimension_fields:
        return "unsupported_dimension"
    if locations & timestamp_fields:
        return "invalid_timestamp"
    return "invalid_schema"


def _validate_model_name(value: str | None) -> str | None:
    """校验 runtime 模型名的稳定字符串边界，不维护具体型号目录。"""

    if value is None:
        return None
    if not value or value != value.strip() or not value.isprintable():
        raise ValueError("invalid model name")
    return value


def _prepare_span(value: TelemetrySpanInput) -> PreparedSpan:
    """把已校验 span 转换为固定宽度、紧凑且不可含正文的行。"""

    started_at_ns = datetime_to_epoch_ns(value.started_at)
    ended_at_ns = datetime_to_epoch_ns(value.ended_at)
    duration_ms = (ended_at_ns - started_at_ns) / 1_000_000
    return PreparedSpan(
        trace_id=bytes.fromhex(value.trace_id),
        span_id=bytes.fromhex(value.span_id),
        parent_span_id=(
            bytes.fromhex(value.parent_span_id)
            if value.parent_span_id is not None
            else None
        ),
        started_at_ns=started_at_ns,
        ended_at_ns=ended_at_ns,
        duration_ms=duration_ms,
        duration_bucket=_duration_bucket(duration_ms),
        component=value.component,
        operation=value.operation,
        status=value.status,
        runtime=value.runtime,
        model=value.model,
        session_ref=_opaque_reference(value.session_ref),
        call_ref=_opaque_reference(value.call_ref),
        attributes_json=json.dumps(
            value.attributes.model_dump(exclude_none=True),
            sort_keys=True,
            separators=(",", ":"),
        ),
        links=tuple(
            PreparedTraceLink(
                trace_id=bytes.fromhex(link.trace_id),
                span_id=bytes.fromhex(link.span_id) if link.span_id else None,
            )
            for link in value.links
        ),
    )


def _prepare_metric(value: TelemetryMetricInput) -> PreparedMetric:
    """把已校验 metric 转换为紧凑且不可含正文的行。"""

    return PreparedMetric(
        metric_id=value.metric_id,
        observed_at_ns=datetime_to_epoch_ns(value.observed_at),
        component=value.component,
        name=value.name,
        kind=value.kind,
        unit=value.unit,
        value=value.value,
        status=value.status,
        runtime=value.runtime,
        model=value.model,
        operation=value.operation,
        attributes_json=json.dumps(
            value.attributes.model_dump(exclude_none=True),
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _opaque_reference(value: str | None) -> bytes | None:
    """把本机引用转换为固定宽度摘要，避免明文进入遥测库。"""

    if value is None:
        return None
    return hashlib.blake2b(value.encode("utf-8"), digest_size=16).digest()


def _duration_bucket(duration_ms: float) -> int:
    """把毫秒耗时映射到 schema v1 固定的非累计 bucket。"""

    from trowel_py.telemetry.catalog import DURATION_HISTOGRAM_UPPER_BOUNDS_MS

    for index, upper in enumerate(DURATION_HISTOGRAM_UPPER_BOUNDS_MS):
        if upper is None or duration_ms <= upper:
            return index
    raise AssertionError("histogram must end with an unbounded bucket")
