"""Memory 来源片段与派生活动的 host-neutral 契约。"""

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
    """一次模型身份观测；basis 说明 model/effort 的证据来自哪里。"""

    model: str
    effort: str = ""
    provider: str = ""
    basis: ModelBasis = "unknown"

    def __post_init__(self) -> None:
        if not any(value.strip() for value in (self.model, self.effort, self.provider)):
            raise ValueError("model identity needs at least one observed value")
        if self.basis not in _MODEL_BASES:
            raise ValueError(f"unknown model identity basis: {self.basis}")


@dataclass(frozen=True)
class CcJsonlSource:
    kind: Literal["cc_jsonl"] = "cc_jsonl"
    locator: str = ""
    start_offset: int = 0
    end_offset: int = 0

    def __post_init__(self) -> None:
        if not self.locator.strip():
            raise ValueError("cc_jsonl source needs a locator")
        if self.start_offset < 0 or self.end_offset <= self.start_offset:
            raise ValueError("cc_jsonl source needs an increasing byte range")


@dataclass(frozen=True)
class CodexTurnsSource:
    kind: Literal["codex_turns"] = "codex_turns"
    turn_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.turn_ids or any(not turn_id.strip() for turn_id in self.turn_ids):
            raise ValueError("codex_turns source needs non-empty turn ids")
        if len(set(self.turn_ids)) != len(self.turn_ids):
            raise ValueError("codex_turns source cannot repeat turn ids")


SegmentSource = CcJsonlSource | CodexTurnsSource


@dataclass(frozen=True)
class CompletedSegment:
    """Memory Kernel 消费的一段已封口原始经历。"""

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
    """一次记忆派生活动；generator 与被提炼会话的模型身份分离。"""

    pipeline: str
    pipeline_version: int
    run_id: str
    generated_at: str
    generator_runtime: GeneratorRuntime
    generator: ModelIdentity | None = None

    def __post_init__(self) -> None:
        if not self.pipeline.strip() or self.pipeline_version < 1:
            raise ValueError("derivation needs a pipeline and positive version")
        if not self.run_id.strip() or not self.generated_at.strip():
            raise ValueError("derivation needs run_id and generated_at")
        if self.generator_runtime not in _GENERATOR_RUNTIMES:
            raise ValueError(f"unknown generator runtime: {self.generator_runtime}")


def model_identity_to_dict(identity: ModelIdentity) -> dict[str, str]:
    return {
        "model": identity.model,
        "effort": identity.effort,
        "provider": identity.provider,
        "basis": identity.basis,
    }


def model_identity_from_dict(value: object) -> ModelIdentity | None:
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
    if isinstance(source, CcJsonlSource):
        return {
            "kind": source.kind,
            "locator": source.locator,
            "start_offset": source.start_offset,
            "end_offset": source.end_offset,
        }
    return {"kind": source.kind, "turn_ids": list(source.turn_ids)}


def segment_source_from_dict(value: object) -> SegmentSource | None:
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
    """只扫描指定 CC JSONL 字节范围，按首次出现顺序返回实际 model。"""

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
