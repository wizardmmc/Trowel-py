"""重生成计划、运行结果与 staged artifact 的稳定数据契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

RegenerationLayer = Literal["daily", "weekly", "monthly"]
RegenerationMode = Literal["missing", "failed", "stale", "all"]
TargetStatus = Literal["success", "failed", "skipped"]


@dataclass(frozen=True)
class RegenerationTarget:
    layer: RegenerationLayer
    period: str
    reason: str
    dependencies: tuple[str, ...] = ()
    live_exists: bool = False
    current_source_hash: str = ""
    expected_source_hash: str = ""
    current_generation_version: int | None = None
    expected_generation_version: int = 0
    differences: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.layer}:{self.period}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "period": self.period,
            "reason": self.reason,
            "dependencies": list(self.dependencies),
            "live_exists": self.live_exists,
            "current_source_hash": self.current_source_hash,
            "expected_source_hash": self.expected_source_hash,
            "current_generation_version": self.current_generation_version,
            "expected_generation_version": self.expected_generation_version,
            "differences": list(self.differences),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RegenerationTarget:
        layer = str(value.get("layer") or "")
        if layer not in {"daily", "weekly", "monthly"}:
            raise ValueError(f"invalid regeneration layer: {layer!r}")
        current_version = value.get("current_generation_version")
        return cls(
            layer=cast("RegenerationLayer", layer),
            period=str(value.get("period") or ""),
            reason=str(value.get("reason") or ""),
            dependencies=tuple(str(item) for item in value.get("dependencies") or ()),
            live_exists=bool(value.get("live_exists")),
            current_source_hash=str(value.get("current_source_hash") or ""),
            expected_source_hash=str(value.get("expected_source_hash") or ""),
            current_generation_version=(
                int(current_version) if current_version is not None else None
            ),
            expected_generation_version=int(
                value.get("expected_generation_version") or 0
            ),
            differences=tuple(str(item) for item in value.get("differences") or ()),
        )


@dataclass(frozen=True)
class RegenerationPlan:
    plan_id: str
    created_at: str
    requested_layer: RegenerationLayer
    from_period: str
    to_period: str
    mode: RegenerationMode
    targets: tuple[RegenerationTarget, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "created_at": self.created_at,
            "requested_layer": self.requested_layer,
            "from_period": self.from_period,
            "to_period": self.to_period,
            "mode": self.mode,
            "targets": [target.to_dict() for target in self.targets],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RegenerationPlan:
        layer = str(value.get("requested_layer") or "")
        mode = str(value.get("mode") or "")
        if layer not in {"daily", "weekly", "monthly"}:
            raise ValueError(f"invalid regeneration layer: {layer!r}")
        if mode not in {"missing", "failed", "stale", "all"}:
            raise ValueError(f"invalid regeneration mode: {mode!r}")
        raw_targets = value.get("targets", [])
        if not isinstance(raw_targets, list):
            raise ValueError("regeneration targets must be a list")
        if any(not isinstance(item, dict) for item in raw_targets):
            raise ValueError("each regeneration target must be an object")
        return cls(
            plan_id=str(value.get("plan_id") or ""),
            created_at=str(value.get("created_at") or ""),
            requested_layer=cast("RegenerationLayer", layer),
            from_period=str(value.get("from_period") or ""),
            to_period=str(value.get("to_period") or ""),
            mode=cast("RegenerationMode", mode),
            targets=tuple(
                RegenerationTarget.from_dict(item)
                for item in raw_targets
            ),
        )


@dataclass(frozen=True)
class StagedArtifact:
    relative_path: str
    action: Literal["write", "delete"]

    def to_dict(self) -> dict[str, str]:
        return {"relative_path": self.relative_path, "action": self.action}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> StagedArtifact:
        action = str(value.get("action") or "")
        if action not in {"write", "delete"}:
            raise ValueError(f"invalid staged artifact action: {action!r}")
        return cls(
            relative_path=str(value.get("relative_path") or ""),
            action=cast("Literal['write', 'delete']", action),
        )


@dataclass(frozen=True)
class RegenerationResult:
    target_key: str
    status: TargetStatus
    artifacts: tuple[StagedArtifact, ...] = ()
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_key": self.target_key,
            "status": self.status,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RegenerationResult:
        status = str(value.get("status") or "")
        if status not in {"success", "failed", "skipped"}:
            raise ValueError(f"invalid regeneration result status: {status!r}")
        raw_artifacts = value.get("artifacts", [])
        if not isinstance(raw_artifacts, list):
            raise ValueError("regeneration artifacts must be a list")
        if any(not isinstance(item, dict) for item in raw_artifacts):
            raise ValueError("each regeneration artifact must be an object")
        return cls(
            target_key=str(value.get("target_key") or ""),
            status=cast("TargetStatus", status),
            artifacts=tuple(
                StagedArtifact.from_dict(item)
                for item in raw_artifacts
            ),
            error=str(value.get("error") or ""),
        )


@dataclass(frozen=True)
class RegenerationRun:
    run_id: str
    plan_id: str
    status: Literal["running", "failed", "completed"]
    staging_root: str
    started_at: str
    updated_at: str
    results: tuple[RegenerationResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "plan_id": self.plan_id,
            "status": self.status,
            "staging_root": self.staging_root,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "results": [result.to_dict() for result in self.results],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RegenerationRun:
        status = str(value.get("status") or "")
        if status not in {"running", "failed", "completed"}:
            raise ValueError(f"invalid regeneration run status: {status!r}")
        raw_results = value.get("results", [])
        if not isinstance(raw_results, list):
            raise ValueError("regeneration results must be a list")
        if any(not isinstance(item, dict) for item in raw_results):
            raise ValueError("each regeneration result must be an object")
        return cls(
            run_id=str(value.get("run_id") or ""),
            plan_id=str(value.get("plan_id") or ""),
            status=cast("Literal['running', 'failed', 'completed']", status),
            staging_root=str(value.get("staging_root") or ""),
            started_at=str(value.get("started_at") or ""),
            updated_at=str(value.get("updated_at") or ""),
            results=tuple(
                RegenerationResult.from_dict(item)
                for item in raw_results
            ),
        )
