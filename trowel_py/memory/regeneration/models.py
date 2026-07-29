"""定义重生成计划、运行记录与 staging 产物的持久化数据契约。

这些 frozen dataclass 只禁止字段重新赋值，直接构造不校验字段语义；各
``from_dict`` 也只执行其文档列出的局部校验。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

RegenerationLayer = Literal["daily", "weekly", "monthly"]
RegenerationMode = Literal["missing", "failed", "stale", "all"]
TargetStatus = Literal["success", "failed", "skipped"]


@dataclass(frozen=True)
class RegenerationTarget:
    """一个需要在隔离副本中重生成的日、周或月派生物。

    Attributes:
        layer: 派生物所属的日、周或月层。
        period: 该层使用的日期、ISO 周或月份标识。
        reason: 入选原因，如 ``missing``、``failed``、``stale``、``all`` 或
            ``upstream``。
        dependencies: 执行本目标前必须成功的目标 key；规划器会排序，恢复时
            则保留输入的迭代顺序。
        live_exists: 制订计划时对应 live 文件是否存在。
        current_source_hash: live 文件记录的来源哈希；缺失时为空。
        expected_source_hash: 当前上游内容的来源哈希；级联目标可能为空。
        current_generation_version: live 文件中的生成版本；规划时缺失或不是
            ``int`` 才记为 ``None``。
        expected_generation_version: 当前实现要求的生成版本。
        differences: ``missing``、``source_hash``、``generation_version``、
            ``generation_status`` 或 ``upstream`` 等差异项。
    """

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
        """返回可在计划和依赖中稳定引用的目标标识。"""
        return f"{self.layer}:{self.period}"

    def to_dict(self) -> dict[str, Any]:
        """把重生成目标转换为可持久化字典。"""
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
        """从字典恢复目标，仅显式校验派生层。

        依赖和差异字段按输入迭代，版本字段通过 ``int`` 转换；本方法不校验
        dependency key、顺序或其他字段间关系。

        Raises:
            ValueError: 派生层未知，或版本值不能转换为整数。
            TypeError: 版本值不支持整数转换，或依赖、差异字段不可迭代。
            OverflowError: 浮点版本值在整数转换时溢出。
        """
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
    """一份不可变且可持久化的重生成计划。

    Attributes:
        plan_id: 计划的唯一标识，也是后续运行的引用键。
        created_at: 制订计划时带时区的 ISO 时间。
        requested_layer: 用户请求检查的起始派生层。
        from_period: ``requested_layer`` 请求范围的首个周期，包含在范围内。
        to_period: ``requested_layer`` 请求范围的末个周期，包含在范围内。
        mode: 目标筛选模式。
        targets: 规划器按日、周、月，再按周期排列的目标；恢复时保留输入顺序。
    """

    plan_id: str
    created_at: str
    requested_layer: RegenerationLayer
    from_period: str
    to_period: str
    mode: RegenerationMode
    targets: tuple[RegenerationTarget, ...]

    def to_dict(self) -> dict[str, Any]:
        """把重生成计划转换为可持久化字典。"""
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
        """从字典恢复计划，并校验层级、模式及目标列表结构。

        本方法不校验周期范围、目标顺序或依赖关系。

        Raises:
            ValueError: 层级或模式未知、``targets`` 不是对象列表，或嵌套目标
                的层级、版本值无效。
            TypeError: 嵌套目标包含不支持转换或迭代的字段。
            OverflowError: 嵌套目标的版本值在整数转换时溢出。
        """
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
    """显式发布时需要写入或删除的一份 staging 产物。

    Attributes:
        relative_path: 同时相对于 staging 根目录和 live memory 根目录的文件路径。
        action: 发布时执行 ``write`` 或 ``delete``。
    """

    relative_path: str
    action: Literal["write", "delete"]

    def to_dict(self) -> dict[str, str]:
        """把 staged 文件操作转换为可持久化字典。"""
        return {"relative_path": self.relative_path, "action": self.action}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> StagedArtifact:
        """从字典恢复文件操作，并拒绝未知 action。

        本方法不校验 ``relative_path``。``apply_regeneration`` 在真正发布前
        分别确认解析后的路径位于 staging 根目录和 live memory 根目录内。
        """
        action = str(value.get("action") or "")
        if action not in {"write", "delete"}:
            raise ValueError(f"invalid staged artifact action: {action!r}")
        return cls(
            relative_path=str(value.get("relative_path") or ""),
            action=cast("Literal['write', 'delete']", action),
        )


@dataclass(frozen=True)
class RegenerationResult:
    """一个重生成目标的执行结果。

    ``status``、``artifacts`` 与 ``error`` 的对应关系由执行器维持，本模型及
    ``from_dict`` 不校验三者是否一致。

    Attributes:
        target_key: 对应的 ``RegenerationTarget.key``。
        status: 目标的成功、失败或因依赖未满足而跳过状态。
        artifacts: 成功执行后需要发布的文件操作。
        error: 失败原因或未满足的依赖；成功时为空。
    """

    target_key: str
    status: TargetStatus
    artifacts: tuple[StagedArtifact, ...] = ()
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """把目标执行结果转换为可持久化字典。"""
        return {
            "target_key": self.target_key,
            "status": self.status,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RegenerationResult:
        """从字典恢复结果，并校验状态及产物列表结构。

        Raises:
            ValueError: 状态或嵌套产物的 action 未知，或 ``artifacts`` 不是
                对象列表。
        """
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
    """一份可断点续跑的 staging 执行记录。

    服务续跑时复用 ``run_id`` 和 ``started_at``，保留已有成功结果，并重试
    先前失败或因依赖跳过的目标；仅在全部目标成功时写入 ``completed``。

    Attributes:
        run_id: 运行标识；同一计划的续跑沿用该值。
        plan_id: 本次运行执行的不可变计划标识。
        status: 运行中、失败或全部目标成功完成。
        staging_root: 本次运行隔离副本的路径字符串。
        started_at: 首次运行开始时带时区的 ISO 时间。
        updated_at: 最近保存状态时带时区的 ISO 时间。
        results: 服务按计划顺序保存的已处理目标结果。
    """

    run_id: str
    plan_id: str
    status: Literal["running", "failed", "completed"]
    staging_root: str
    started_at: str
    updated_at: str
    results: tuple[RegenerationResult, ...]

    def to_dict(self) -> dict[str, Any]:
        """把重生成运行记录转换为可持久化字典。"""
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
        """从字典恢复运行，并校验状态及结果列表结构。

        本方法不校验状态与结果是否一致、结果顺序或 ``staging_root``，嵌套
        结果的校验异常会直接透传。

        Raises:
            ValueError: 状态未知、``results`` 不是对象列表，或嵌套结果无效。
        """
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
