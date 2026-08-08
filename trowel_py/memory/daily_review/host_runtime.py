"""共享 Daily Review 派生 Agent 的创建、驱动和 provenance 生成。"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from trowel_py.memory.daily_review.models import ReviewSessionLike
from trowel_py.memory.provenance import DerivationProvenance, ModelIdentity

ReviewHostFactory = Callable[[ReviewSessionLike, Path], Any]
DEFAULT_REVIEW_MODEL = "glm-5.1"


async def drive_review_host(host: Any, prompt: str) -> bool:
    """发送一轮提示并报告事件流是否出现正常完成事件。

    Args:
        host: 提供异步 ``send`` 事件流的隔离 Agent host。
        prompt: 本轮要求 Agent 执行的完整提示。

    Returns:
        事件流中至少出现一次 ``finished`` 时为 True。
    """

    finished = False
    async for event in host.send(prompt):
        if getattr(event, "type", None) == "finished":
            finished = True
    return finished


def create_review_host(
    session: ReviewSessionLike,
    workdir: Path,
    host_factory: ReviewHostFactory | None,
) -> Any:
    """通过注入工厂或固定模型创建隔离的 Review host。

    Args:
        session: 只向测试或自定义工厂提供的来源会话身份。
        workdir: 当前派生任务唯一允许写入的工作目录。
        host_factory: 测试或应用生命周期注入的 host 工厂；None 时直接创建
            Claude Code review 会话。

    Returns:
        提供异步 ``send``，并可选提供 ``close`` 的 host。
    """

    if host_factory is not None:
        return host_factory(session, workdir)

    from trowel_py.cc_host.service import CCHost
    from trowel_py.memory.mcp_config import write_mcp_config

    return CCHost(
        session_id=uuid.uuid4().hex,
        workdir=str(workdir),
        model=DEFAULT_REVIEW_MODEL,
        session_kind="review",
        mcp_config=str(write_mcp_config()),
    )


def review_derivation(
    host: Any,
    *,
    pipeline: str,
    pipeline_version: int,
) -> DerivationProvenance:
    """从真实 host 配置构造一个派生任务的 provenance。

    Args:
        host: 已生成并通过结构门禁的 Agent host。
        pipeline: 当前派生任务的稳定名字。
        pipeline_version: 当前 prompt 和解析契约的正整数版本。

    Returns:
        带运行 ID、生成时间和可选模型配置的派生记录。
    """

    model = getattr(host, "model", None)
    effort = getattr(host, "effort", None)
    generator = (
        ModelIdentity(
            model=str(model or ""),
            effort=str(effort or ""),
            basis="host_config",
        )
        if any(isinstance(value, str) and value.strip() for value in (model, effort))
        else None
    )
    run_id = getattr(host, "session_id", None)
    return DerivationProvenance(
        pipeline=pipeline,
        pipeline_version=pipeline_version,
        run_id=str(run_id or uuid.uuid4().hex),
        generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        generator_runtime=str(getattr(host, "runtime", "claude_code")),
        generator=generator,
    )
