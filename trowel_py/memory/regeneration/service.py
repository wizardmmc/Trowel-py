"""在隔离 staging 中执行重生成计划，并显式发布到 live memory。"""

from __future__ import annotations

import contextlib
import fcntl
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from trowel_py.llm.client import LLMProvider
from trowel_py.memory.compress import compress_daily, compress_monthly, compress_weekly
from trowel_py.memory.store import _split_frontmatter

from .models import (
    RegenerationResult,
    RegenerationRun,
    RegenerationTarget,
    StagedArtifact,
)
from .storage import (
    apply_path,
    atomic_write_json,
    load_plan,
    load_run,
    read_json,
    run_path,
    run_root,
    save_run,
)

_LAYER_ORDER = {"daily": 0, "weekly": 1, "monthly": 2}


def _now() -> str:
    """返回带本地时区的当前 ISO 时间。"""
    return datetime.now().astimezone().isoformat()


def _default_provider() -> LLMProvider:
    """按项目配置创建 OpenAI 或 Anthropic provider。"""
    from trowel_py.config import load_llm_config
    from trowel_py.llm.client import AnthropicProvider, OpenAIProvider

    config = load_llm_config()
    if config.provider == "openai":
        return OpenAIProvider(config)
    return AnthropicProvider(config)


@contextlib.contextmanager
def _exclusive_lock(path: Path):
    """创建锁文件并在上下文期间持有阻塞式进程独占锁。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _prepare_staging(root: Path, staging: Path) -> None:
    """用 live episodes 和 diary 初始化可续跑的 staging。

    ``.ready`` 存在时直接复用当前副本，不再从 live 刷新；否则先在同级临时
    目录复制这两个子目录，再整体替换 staging。其他 memory 内容不会复制。
    """
    ready = staging / ".ready"
    if ready.is_file():
        return
    staging.parent.mkdir(parents=True, exist_ok=True)
    temporary = staging.with_name(f".staging.{uuid.uuid4().hex}.tmp")
    try:
        temporary.mkdir()
        for name in ("episodes", "diary"):
            source = root / name
            if source.is_dir():
                shutil.copytree(source, temporary / name)
        (temporary / ".ready").write_text("ready\n", encoding="utf-8")
        if staging.exists():
            shutil.rmtree(staging)
        os.replace(temporary, staging)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def _derived_path(root: Path, target: RegenerationTarget) -> Path:
    """返回目标在给定 live 或 staging 根目录下的日记路径。"""
    directory = {
        "daily": "daily",
        "weekly": "weekly",
        "monthly": "monthly",
    }[target.layer]
    return root / "diary" / directory / f"{target.period}.md"


def _relative(path: Path, root: Path) -> str:
    """返回文件相对根目录的 POSIX 路径。

    Raises:
        ValueError: 文件不在根目录内。
    """
    return path.relative_to(root).as_posix()


def _safe_child(root: Path, relative_path: str) -> Path:
    """解析受根目录约束的路径。

    绝对路径、包含 ``..`` 的路径，以及解析符号链接后逃出根目录的路径都会
    被拒绝。

    Raises:
        ValueError: 路径可能逃出根目录。
    """
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe staged artifact path: {relative_path!r}")
    root_resolved = root.resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root_resolved):
        raise ValueError(f"unsafe staged artifact path: {relative_path!r}")
    return candidate


def _validated_artifact(
    staging: Path,
    target: RegenerationTarget,
) -> tuple[StagedArtifact, ...]:
    """校验目标产物并返回需要执行的发布操作。

    主日记必须存在，且 frontmatter 的状态为 ``ok``、版本符合计划、来源哈希
    非空；计划带有预期哈希时还必须完全匹配。周目标除主日记写入外，还会为
    三类 bypass 文件分别生成写入或删除操作，但不校验 bypass 内容。

    Raises:
        ValueError: 主产物缺失或其生成元数据不符合计划。
        UnicodeError: 主产物不是有效 UTF-8。
        OSError: 读取主产物失败。
    """
    path = _derived_path(staging, target)
    if not path.is_file():
        raise ValueError("compressor did not write the target artifact")
    frontmatter, _body = _split_frontmatter(path.read_text(encoding="utf-8"))
    if not frontmatter or frontmatter.get("generation_status") != "ok":
        raise ValueError("compressor did not produce generation_status=ok")
    if frontmatter.get("generation_version") != target.expected_generation_version:
        raise ValueError("compressor wrote an unexpected generation version")
    written_hash = str(frontmatter.get("source_hash") or "")
    if not written_hash:
        raise ValueError("compressor did not write a source hash")
    if target.expected_source_hash and written_hash != target.expected_source_hash:
        raise ValueError("compressor wrote an unexpected source hash")
    artifacts = [StagedArtifact(_relative(path, staging), "write")]
    if target.layer == "weekly":
        for category in (
            "technical-detail",
            "emotional-trigger",
            "cross-week-causal",
        ):
            bypass = (
                staging
                / "diary"
                / "bypass"
                / category
                / f"{target.period}.md"
            )
            artifacts.append(
                StagedArtifact(
                    _relative(bypass, staging),
                    "write" if bypass.is_file() else "delete",
                )
            )
    return tuple(artifacts)


def _execute_target(
    staging: Path,
    target: RegenerationTarget,
    provider: LLMProvider,
    run_id: str,
) -> tuple[StagedArtifact, ...]:
    """强制执行一个目标，并校验其 staging 产物。

    周、月压缩器还必须在报告中确认对应文件已写入；目标层由已校验的计划
    提供，除 ``daily`` 和 ``weekly`` 外按 ``monthly`` 执行。
    """
    if target.layer == "daily":
        compress_daily(staging, target.period, provider, force=True)
    elif target.layer == "weekly":
        report = compress_weekly(
            staging,
            target.period,
            provider,
            force=True,
            run_id=run_id,
        )
        if not report.get("weekly_written"):
            raise ValueError(
                f"weekly generation failed: {report.get('generation_status', 'unknown')}"
            )
    else:
        report = compress_monthly(
            staging,
            target.period,
            provider,
            force=True,
            run_id=run_id,
        )
        if not report.get("monthly_written"):
            raise ValueError(
                f"monthly generation failed: {report.get('generation_status', 'unknown')}"
            )
    return _validated_artifact(staging, target)


def run_regeneration(
    root: Path | str,
    plan_id: str,
    *,
    target: str = "staging",
    provider: LLMProvider | None = None,
) -> RegenerationRun:
    """在进程独占锁内执行或续跑一份重生成计划。

    当前仅接受 ``target="staging"``。同一 ``plan_id`` 固定映射到同一个 run；
    已完成的 run 原样返回，未完成的 run 保留成功结果并重试失败或跳过的目标。
    默认 provider 创建失败会直接中止本次续跑并保留已保存的 ``running`` 状态，
    不会转换为目标失败结果。

    Args:
        root: memory 根目录。
        plan_id: 已持久化的计划标识。
        target: 执行目标，目前只能是 ``staging``。
        provider: 压缩使用的 LLM provider；需要执行目标且未提供时才按配置创建。

    Returns:
        完成或失败的最终运行记录。

    Raises:
        ValueError: target 不受支持，或计划、运行记录无效。
        OSError: 获取锁、准备 staging 或读写运行文件失败。
    """
    if target != "staging":
        raise ValueError("regeneration v0 only supports target='staging'")
    root_path = Path(root)
    lock_path = run_root(root_path, f"run-{plan_id}") / "run.lock"
    with _exclusive_lock(lock_path):
        return _run_regeneration(
            root_path,
            plan_id,
            provider=provider,
        )


def _run_regeneration(
    root: Path,
    plan_id: str,
    *,
    provider: LLMProvider | None,
) -> RegenerationRun:
    """执行或续跑计划，并在每个已处理目标后保存运行状态。

    已有成功结果不会重做；依赖未全部成功的目标记为 ``skipped``。单个目标的
    压缩或产物校验异常记为 ``failed``，不阻止后续无依赖目标；默认 provider
    在此捕获边界外创建，创建失败会直接中止。只有全部目标成功，最终状态才是
    ``completed``，空计划也视为完成。
    """
    root_path = root
    plan = load_plan(root_path, plan_id)
    run_id = f"run-{plan.plan_id}"
    manifest_path = run_path(root_path, run_id)
    previous: RegenerationRun | None = None
    if manifest_path.is_file():
        previous = load_run(root_path, run_id)
        if previous.status == "completed":
            return previous
    staging = run_root(root_path, run_id) / "staging"
    _prepare_staging(root_path, staging)
    started_at = previous.started_at if previous else _now()
    successful = {
        result.target_key: result
        for result in (previous.results if previous else ())
        if result.status == "success"
    }
    results: dict[str, RegenerationResult] = dict(successful)
    current_run = RegenerationRun(
        run_id=run_id,
        plan_id=plan.plan_id,
        status="running",
        staging_root=str(staging),
        started_at=started_at,
        updated_at=_now(),
        results=tuple(results.values()),
    )
    save_run(root_path, current_run)
    active_provider = provider

    for item in plan.targets:
        if item.key in successful:
            continue
        unmet = [
            dependency
            for dependency in item.dependencies
            if results.get(dependency) is None
            or results[dependency].status != "success"
        ]
        if unmet:
            results[item.key] = RegenerationResult(
                target_key=item.key,
                status="skipped",
                error=f"unmet dependencies: {', '.join(unmet)}",
            )
        else:
            if active_provider is None:
                active_provider = _default_provider()
            # 压缩和产物校验异常转成结果，后续无依赖目标仍可执行并保存进度。
            try:
                artifacts = _execute_target(staging, item, active_provider, run_id)
            except Exception as exc:  # noqa: BLE001
                results[item.key] = RegenerationResult(
                    target_key=item.key,
                    status="failed",
                    error=str(exc),
                )
            else:
                results[item.key] = RegenerationResult(
                    target_key=item.key,
                    status="success",
                    artifacts=artifacts,
                )
        ordered_results = tuple(
            results[target.key] for target in plan.targets if target.key in results
        )
        current_run = RegenerationRun(
            run_id=run_id,
            plan_id=plan.plan_id,
            status="running",
            staging_root=str(staging),
            started_at=started_at,
            updated_at=_now(),
            results=ordered_results,
        )
        save_run(root_path, current_run)

    ordered_results = tuple(
        results[target.key] for target in plan.targets if target.key in results
    )
    completed = len(ordered_results) == len(plan.targets) and all(
        result.status == "success" for result in ordered_results
    )
    final = RegenerationRun(
        run_id=run_id,
        plan_id=plan.plan_id,
        status="completed" if completed else "failed",
        staging_root=str(staging),
        started_at=started_at,
        updated_at=_now(),
        results=ordered_results,
    )
    save_run(root_path, final)
    return final


def _publish_artifact(path: Path, content: bytes | None) -> None:
    """原子替换一份 live 文件；内容为 ``None`` 时直接删除。"""
    if content is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _restore_artifact(path: Path, content: bytes | None) -> None:
    """原子恢复 live 文件；原先不存在时将其删除。"""
    if content is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.rollback")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def apply_regeneration(root: Path | str, run_id: str) -> dict[str, Any]:
    """把 completed run 的 staging 产物显式发布到 live memory。

    发布前会核对 run、计划、全部成功结果、固定 staging 位置和每条产物路径。
    同一 run 已有 apply 记录时直接返回该记录。发布操作在全局 apply 锁内执行；
    某项发布失败时，会逆序恢复此前已成功发布的路径。发布完成后写入 apply
    记录；写记录失败不会自动回滚已发布文件。

    Args:
        root: live memory 根目录。
        run_id: 已完成的重生成运行标识。

    Returns:
        包含计划、时间、涉及层级和产物操作数量的幂等发布报告。

    Raises:
        ValueError: run 未完成、staging 位置不符、结果缺失或产物路径不安全。
        FileNotFoundError: run、计划或声明写入的 staging 产物不存在。
        OSError: 读取、发布、恢复或保存报告失败。
    """
    root_path = Path(root)
    applied_path = apply_path(root_path, run_id)
    if applied_path.is_file():
        return read_json(applied_path)
    run = load_run(root_path, run_id)
    if run.status != "completed":
        raise ValueError(f"regeneration run is not completed: {run.status}")
    plan = load_plan(root_path, run.plan_id)
    results = {result.target_key: result for result in run.results}
    staging = Path(run.staging_root)
    expected_staging = run_root(root_path, run_id) / "staging"
    if staging.resolve() != expected_staging.resolve():
        raise ValueError("regeneration run points outside its staging directory")
    operations: list[tuple[Path, bytes | None]] = []
    layers: list[str] = []
    for target in sorted(
        plan.targets, key=lambda item: (_LAYER_ORDER[item.layer], item.period)
    ):
        result = results.get(target.key)
        if result is None or result.status != "success":
            raise ValueError(f"missing successful result for {target.key}")
        if target.layer not in layers:
            layers.append(target.layer)
        for artifact in result.artifacts:
            staged_path = _safe_child(staging, artifact.relative_path)
            if artifact.action == "write":
                if not staged_path.is_file():
                    raise FileNotFoundError(
                        f"staged artifact disappeared: {artifact.relative_path}"
                    )
                content: bytes | None = staged_path.read_bytes()
            else:
                content = None
            operations.append(
                (_safe_child(root_path, artifact.relative_path), content)
            )

    lock_path = root_path / "meta" / "regeneration" / "apply.lock"
    with _exclusive_lock(lock_path):
        if applied_path.is_file():
            return read_json(applied_path)
        backups = {
            path: path.read_bytes() if path.is_file() else None
            for path, _content in operations
        }
        published: list[Path] = []
        try:
            for path, content in operations:
                _publish_artifact(path, content)
                published.append(path)
        except Exception:
            for path in reversed(published):
                _restore_artifact(path, backups[path])
            raise
        report: dict[str, Any] = {
            "run_id": run_id,
            "plan_id": run.plan_id,
            "applied": True,
            "applied_at": _now(),
            "layers": layers,
            "artifacts": len(operations),
        }
        atomic_write_json(applied_path, report)
        return report
