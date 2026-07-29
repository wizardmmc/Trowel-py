"""定义跨 host 的 Memory 来源片段、派生活动及其宽松持久化 codec。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Iterator, Literal, cast

HostKind = Literal["claude_code", "codex"]
ModelBasis = Literal["runtime_event", "host_config", "binding", "unknown"]
GeneratorRuntime = Literal["claude_code", "codex", "direct_api", "unknown"]

_HOST_KINDS = frozenset({"claude_code", "codex"})
_MODEL_BASES = frozenset({"runtime_event", "host_config", "binding", "unknown"})
_GENERATOR_RUNTIMES = frozenset({"claude_code", "codex", "direct_api", "unknown"})


@dataclass(frozen=True)
class ModelIdentity:
    """记录一次模型身份观测。

    Attributes:
        model: 观测到的模型名称，可为空。
        effort: 观测到的推理强度，可为空。
        provider: 观测到的提供方，可为空。
        basis: model、effort 和 provider 的证据来源。
    """

    model: str
    effort: str = ""
    provider: str = ""
    basis: ModelBasis = "unknown"

    def __post_init__(self) -> None:
        """要求至少一个身份字段非空白，并校验 basis 闭集。

        Raises:
            ValueError: 三个身份字段均为空白，或 basis 未知。
        """
        if not any(value.strip() for value in (self.model, self.effort, self.provider)):
            raise ValueError("model identity needs at least one observed value")
        if self.basis not in _MODEL_BASES:
            raise ValueError(f"unknown model identity basis: {self.basis}")


@dataclass(frozen=True)
class CcJsonlSource:
    """记录 Claude Code JSONL 中的半开字节范围。

    Attributes:
        kind: 来源判别值；构造时不校验，默认 ``cc_jsonl``。
        locator: JSONL 路径文本，只要求去空白后非空。
        start_offset: 起始字节偏移，必须非负。
        end_offset: 结束字节偏移，必须大于起点。
    """

    kind: Literal["cc_jsonl"] = "cc_jsonl"
    locator: str = ""
    start_offset: int = 0
    end_offset: int = 0

    def __post_init__(self) -> None:
        """校验 locator 和递增字节边界，不访问实际文件。

        Raises:
            ValueError: locator 为空白，起点为负，或终点不大于起点。
        """
        if not self.locator.strip():
            raise ValueError("cc_jsonl source needs a locator")
        if self.start_offset < 0 or self.end_offset <= self.start_offset:
            raise ValueError("cc_jsonl source needs an increasing byte range")


@dataclass(frozen=True)
class CodexTurnsSource:
    """记录一个或多个已封口 Codex 轮次。

    Attributes:
        kind: 来源判别值；构造时不校验，默认 ``codex_turns``。
        turn_ids: 保持原顺序的非空、非空白且不重复轮次 ID。
    """

    kind: Literal["codex_turns"] = "codex_turns"
    turn_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """校验轮次列表非空、各 ID 非空白且不重复。

        Raises:
            ValueError: 没有轮次、存在空白 ID 或重复 ID。
        """
        if not self.turn_ids or any(not turn_id.strip() for turn_id in self.turn_ids):
            raise ValueError("codex_turns source needs non-empty turn ids")
        if len(set(self.turn_ids)) != len(self.turn_ids):
            raise ValueError("codex_turns source cannot repeat turn ids")


SegmentSource = CcJsonlSource | CodexTurnsSource


@dataclass(frozen=True)
class CompletedSegment:
    """Memory Kernel 消费的一段已封口原始经历。

    只校验片段 ID、原生会话 ID、host 闭集和 host/source 类型匹配；其他文本、
    时间、会话 ID 和模型元组不做格式、去重或元素类型校验。

    Attributes:
        segment_id: 跨持久化层引用的片段 ID。
        host_kind: 来源 host，限 ``claude_code`` 或 ``codex``。
        native_session_id: host 原生会话 ID。
        session_kind: 会话用途文本。
        workdir: 原会话工作目录文本。
        registered_at: 会话注册时间文本。
        completed_at: 片段完成时间文本。
        source: 与 host 匹配的 CC 字节范围或 Codex 轮次来源。
        trowel_session_ids: 关联的 Trowel 会话 ID，保持调用方顺序。
        source_models: 来源片段中观测到的模型身份，保持调用方顺序。
    """

    segment_id: str
    host_kind: HostKind
    native_session_id: str
    session_kind: str
    workdir: str
    registered_at: str
    completed_at: str
    source: SegmentSource
    trowel_session_ids: tuple[str, ...] = ()
    source_models: tuple[ModelIdentity, ...] = ()

    def __post_init__(self) -> None:
        """校验必要身份、host 闭集及 host/source 类型对应关系。

        Raises:
            ValueError: 必要 ID 为空白、host 未知或来源类型与 host 不匹配。
        """
        if not self.segment_id.strip():
            raise ValueError("completed segment needs a segment_id")
        if not self.native_session_id.strip():
            raise ValueError("completed segment needs a native_session_id")
        if self.host_kind not in _HOST_KINDS:
            raise ValueError(f"unknown completed segment host: {self.host_kind}")
        if self.host_kind == "claude_code" and not isinstance(
            self.source, CcJsonlSource
        ):
            raise ValueError("claude_code segment needs cc_jsonl source")
        if self.host_kind == "codex" and not isinstance(self.source, CodexTurnsSource):
            raise ValueError("codex segment needs codex_turns source")


@dataclass(frozen=True)
class DerivationProvenance:
    """记录一次记忆派生活动，并与来源会话模型身份分离。

    Attributes:
        pipeline: 派生流水线名称，须非空白。
        pipeline_version: 流水线版本，须至少为 1。
        run_id: 本次派生运行 ID，须非空白。
        generated_at: 生成时间文本，须非空白但不解析。
        generator_runtime: 执行派生的 runtime 闭集值。
        generator: 可选的生成模型身份；构造时不校验对象类型。
    """

    pipeline: str
    pipeline_version: int
    run_id: str
    generated_at: str
    generator_runtime: GeneratorRuntime
    generator: ModelIdentity | None = None

    def __post_init__(self) -> None:
        """校验流水线、运行身份和 generator runtime。

        Raises:
            ValueError: 流水线或运行字段无效，或 runtime 未知。
        """
        if not self.pipeline.strip() or self.pipeline_version < 1:
            raise ValueError("derivation needs a pipeline and positive version")
        if not self.run_id.strip() or not self.generated_at.strip():
            raise ValueError("derivation needs run_id and generated_at")
        if self.generator_runtime not in _GENERATOR_RUNTIMES:
            raise ValueError(f"unknown generator runtime: {self.generator_runtime}")


def model_identity_to_dict(identity: ModelIdentity) -> dict[str, str]:
    """把模型身份的四个字段原样复制到新字典。

    Args:
        identity: 已构造的模型身份。

    Returns:
        按 model、effort、provider、basis 排列的字典。
    """
    return {
        "model": identity.model,
        "effort": identity.effort,
        "provider": identity.provider,
        "basis": identity.basis,
    }


def model_identity_from_dict(value: object) -> ModelIdentity | None:
    """宽松读取模型身份，无效记录返回 ``None``。

    非字典、未知 basis 或三个身份字段均为空时拒绝。basis 缺失或为假值时取
    ``unknown``；model、effort、provider 的假值变为空字符串，其余值直接
    字符串化。

    Args:
        value: 待解析的持久化值。

    Returns:
        通过 ``ModelIdentity`` 校验的对象，否则为 ``None``。
    """
    if not isinstance(value, dict):
        return None
    try:
        basis = str(value.get("basis") or "unknown")
        if basis not in _MODEL_BASES:
            return None
        return ModelIdentity(
            model=str(value.get("model") or ""),
            effort=str(value.get("effort") or ""),
            provider=str(value.get("provider") or ""),
            basis=cast("ModelBasis", basis),
        )
    except (TypeError, ValueError):
        return None


def segment_source_to_dict(source: SegmentSource) -> dict[str, Any]:
    """把来源判别字段和载荷复制到新字典。

    ``CcJsonlSource`` 写入 locator 与两个 offset；其他对象按 Codex 来源处理
    并访问 ``kind``、``turn_ids``，函数不做额外类型校验。

    Args:
        source: 要编码的来源对象。

    Returns:
        CC 来源字典，或带新 turn ID 列表的 Codex 来源字典。
    """
    if isinstance(source, CcJsonlSource):
        return {
            "kind": source.kind,
            "locator": source.locator,
            "start_offset": source.start_offset,
            "end_offset": source.end_offset,
        }
    return {"kind": source.kind, "turn_ids": list(source.turn_ids)}


def segment_source_from_dict(value: object) -> SegmentSource | None:
    """按 kind 宽松读取 CC 或 Codex 来源。

    CC offset 使用 ``int(raw or 0)`` 转换，因此 bool、数字字符串和有限浮点数
    可被接受，浮点数向零截断；构造器随后校验非空 locator 和递增范围。
    Codex turn_ids 只接受 list/tuple，各项直接字符串化，再校验非空和去重。
    未知 kind、常见转换失败或构造校验失败返回 ``None``。

    Args:
        value: 待解析的持久化值。

    Returns:
        有效来源对象，否则为 ``None``。

    Raises:
        OverflowError: offset 的 ``int`` 转换溢出，例如原值为正负无穷。
    """
    if not isinstance(value, dict):
        return None
    try:
        if value.get("kind") == "cc_jsonl":
            return CcJsonlSource(
                locator=str(value.get("locator") or ""),
                start_offset=int(value.get("start_offset") or 0),
                end_offset=int(value.get("end_offset") or 0),
            )
        if value.get("kind") == "codex_turns":
            turn_ids = value.get("turn_ids")
            if not isinstance(turn_ids, (list, tuple)):
                return None
            return CodexTurnsSource(turn_ids=tuple(str(item) for item in turn_ids))
    except (TypeError, ValueError):
        return None
    return None


def completed_segment_to_dict(segment: CompletedSegment) -> dict[str, Any]:
    """按稳定字段顺序把已封口片段编码为新字典。

    Args:
        segment: 要编码的片段。

    Returns:
        包含新会话 ID 列表、来源字典和模型身份字典列表的完整记录。
    """
    return {
        "segment_id": segment.segment_id,
        "host_kind": segment.host_kind,
        "native_session_id": segment.native_session_id,
        "trowel_session_ids": list(segment.trowel_session_ids),
        "session_kind": segment.session_kind,
        "workdir": segment.workdir,
        "registered_at": segment.registered_at,
        "completed_at": segment.completed_at,
        "source": segment_source_to_dict(segment.source),
        "source_models": [
            model_identity_to_dict(identity) for identity in segment.source_models
        ],
    }


def completed_segment_from_dict(value: object) -> CompletedSegment | None:
    """宽松读取已封口片段，无效顶层或必要字段返回 ``None``。

    host 先字符串化并校验，source 必须能独立解析。两个列表字段的假值按空
    tuple 处理，真值则必须是 list/tuple；会话 ID 逐项字符串化，无效模型
    身份条目静默丢弃。其他文本字段的假值变为空字符串，最终由
    ``CompletedSegment`` 校验必要 ID 和 host/source 对应关系。

    Args:
        value: 待解析的持久化值。

    Returns:
        有效片段，否则为 ``None``。

    Raises:
        OverflowError: CC 来源 offset 的 ``int`` 转换溢出。
    """
    if not isinstance(value, dict):
        return None
    host_kind = str(value.get("host_kind") or "")
    if host_kind not in _HOST_KINDS:
        return None
    source = segment_source_from_dict(value.get("source"))
    if source is None:
        return None
    trowel_ids = value.get("trowel_session_ids") or ()
    source_models = value.get("source_models") or ()
    if not isinstance(trowel_ids, (list, tuple)) or not isinstance(
        source_models, (list, tuple)
    ):
        return None
    try:
        return CompletedSegment(
            segment_id=str(value.get("segment_id") or ""),
            host_kind=cast("HostKind", host_kind),
            native_session_id=str(value.get("native_session_id") or ""),
            trowel_session_ids=tuple(str(item) for item in trowel_ids),
            session_kind=str(value.get("session_kind") or ""),
            workdir=str(value.get("workdir") or ""),
            registered_at=str(value.get("registered_at") or ""),
            completed_at=str(value.get("completed_at") or ""),
            source=source,
            source_models=tuple(
                identity
                for item in source_models
                if (identity := model_identity_from_dict(item)) is not None
            ),
        )
    except (TypeError, ValueError):
        return None


def derivation_to_dict(derivation: DerivationProvenance) -> dict[str, Any]:
    """把派生活动编码为新字典。

    generator 为 ``None`` 时省略该键，否则写入模型身份字典。

    Args:
        derivation: 要编码的派生活动。

    Returns:
        包含流水线、运行身份、runtime 和可选 generator 的字典。
    """
    out: dict[str, Any] = {
        "pipeline": derivation.pipeline,
        "pipeline_version": derivation.pipeline_version,
        "run_id": derivation.run_id,
        "generated_at": derivation.generated_at,
        "generator_runtime": derivation.generator_runtime,
    }
    if derivation.generator is not None:
        out["generator"] = model_identity_to_dict(derivation.generator)
    return out


def derivation_from_dict(value: object) -> DerivationProvenance | None:
    """宽松读取派生活动，无效记录返回 ``None``。

    generator runtime 缺失或为假值时取 ``unknown``，其他未知值拒绝。
    pipeline version 使用 ``int(raw or 0)``，因此 bool、数字字符串和有限浮点
    数可被接受；generator 无效时静默降为 ``None``，不使整条派生记录失败。

    Args:
        value: 待解析的持久化值。

    Returns:
        通过 ``DerivationProvenance`` 校验的对象，否则为 ``None``。

    Raises:
        OverflowError: pipeline version 的 ``int`` 转换溢出，例如原值为正负
            无穷。
    """
    if not isinstance(value, dict):
        return None
    try:
        generator_runtime = str(value.get("generator_runtime") or "unknown")
        if generator_runtime not in _GENERATOR_RUNTIMES:
            return None
        return DerivationProvenance(
            pipeline=str(value.get("pipeline") or ""),
            pipeline_version=int(value.get("pipeline_version") or 0),
            run_id=str(value.get("run_id") or ""),
            generated_at=str(value.get("generated_at") or ""),
            generator_runtime=cast("GeneratorRuntime", generator_runtime),
            generator=model_identity_from_dict(value.get("generator")),
        )
    except (TypeError, ValueError):
        return None


def extract_cc_source_models(
    jsonl_path: Path | str,
    start: int,
    end: int,
) -> tuple[ModelIdentity, ...]:
    """扫描 CC JSONL 范围内 assistant 事件声明的模型。

    路径不是普通文件，或 ``end <= max(start, 0)`` 时返回空。只处理完整落在
    范围内的 JSON 对象行及 ``type == "assistant"`` 且 message 为字典的事件。
    model 必须是非空白字符串；去重按未经裁剪的原字符串精确比较，并保持首次
    出现顺序。坏 UTF-8、坏 JSON 和非对象 JSON 逐行跳过。打开或迭代期间的
    ``OSError`` 返回空元组，并丢弃此前已收集的模型；``path.is_file()`` 位于
    捕获范围之外，其未自行忽略的异常向上传播。

    Args:
        jsonl_path: CC JSONL 路径或路径文本；不展开 ``~``。
        start: 起始字节偏移；负值按 0。
        end: 排他的范围终点。

    Returns:
        basis 均为 ``runtime_event`` 的模型身份元组。
    """

    path = Path(jsonl_path)
    if not path.is_file() or end <= max(start, 0):
        return ()
    seen: set[str] = set()
    models: list[ModelIdentity] = []
    try:
        with path.open("rb") as stream:
            for obj in _iter_json_objects_in_range(stream, start, end):
                if obj.get("type") != "assistant":
                    continue
                message = obj.get("message")
                if not isinstance(message, dict):
                    continue
                model = message.get("model")
                if (
                    not isinstance(model, str)
                    or not model.strip()
                    or model in seen
                ):
                    continue
                seen.add(model)
                models.append(ModelIdentity(model=model, basis="runtime_event"))
    except OSError:
        return ()
    return tuple(models)


def _iter_json_objects_in_range(
    stream: IO[bytes], start: int, end: int
) -> Iterator[dict[str, Any]]:
    """迭代完整落在半开字节范围内的 JSON 对象行。

    负起点按 0。正起点若落在一行中间，会先跳过该残行；前一字节是换行时从
    当前字节开始。完整行结束位置等于 end 时仍解析，大于 end 时丢弃并停止。
    JSON/UTF-8 坏行和非对象 JSON 跳过，流定位或读取产生的异常向上传播。

    Args:
        stream: 支持 seek、tell 和 readline 的二进制流。
        start: 起始字节偏移。
        end: 排他的范围终点。

    Yields:
        按文件顺序解析出的 JSON 对象。
    """
    start = max(start, 0)
    stream.seek(start)
    if start > 0:
        stream.seek(start - 1)
        if stream.read(1) != b"\n":
            stream.seek(start)
            stream.readline()
        else:
            stream.seek(start)
    while stream.tell() < end:
        line = stream.readline()
        if not line or stream.tell() > end:
            break
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(value, dict):
            yield value
