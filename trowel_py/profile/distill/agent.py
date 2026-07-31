"""驱动 Profile 提炼 agent，并对其建议草稿执行 Python 门禁。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable

from trowel_py.profile.distill.gate import (
    DistillError,
    GatedDraft,
    parse_and_gate_draft,
)
from trowel_py.profile.distill.models import EvidenceValidator

HostFactory = Callable[[str, Path], Any]

_DISTILL_WORKDIR_NAME = "distill-work"
_DRAFT_FILE = "suggestions-draft.json"


def _ensure_distill_workdir(date_str: str, memory_root: Path) -> Path:
    """创建并返回指定日期的 Profile 提炼工作目录。

    Args:
        date_str: 作为工作目录末级名称的处理日期。
        memory_root: Memory 根目录；工作目录创建在其同级 ``distill-work`` 下。

    Returns:
        已确保存在的 ``<memory_root.parent>/distill-work/<date_str>``。

    Raises:
        OSError: 无法创建工作目录。
    """
    workdir = memory_root.parent / _DISTILL_WORKDIR_NAME / date_str
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


async def drive_and_gate(
    source_id: str,
    workdir: Path,
    prompt: str,
    *,
    proxy_base_url: str,
    settings_path: Path | str | None,
    host_factory: HostFactory | None,
    date_str: str,
    evidence_validator: EvidenceValidator | None = None,
    session_registrar: Any = None,
) -> GatedDraft:
    """为一个运行时无关来源驱动生成 host，并读取和校验建议草稿。

    自定义 ``host_factory`` 只接收来源身份和工作目录，并自行负责其他运行时
    配置。未提供 factory 时创建 ``session_kind="distill"`` 的 CCHost，把代理、
    settings 和 registrar 原样传入；此时 ``session_registrar=None`` 会使用
    默认持久化注册器，隔离重放必须显式传入 no-op registrar。

    函数消费完整事件流。事件流正常结束、期间至少观察到一个
    ``type="finished"`` 事件，并且 host 提供的 ``close``（如有）成功执行后，
    才读取 ``suggestions-draft.json`` 并执行门禁；没有完成事件或 draft 文件
    时抛出 ``DistillError``。只要 host 提供 ``close``，事件流正常结束或抛错
    后都会异步关闭；factory、发送和关闭异常原样传播。工作目录中的旧 draft
    不会预先删除，调用方须使用干净目录或确保 host 覆盖文件，避免把陈旧草稿
    当成本次输出。本函数不持久化通过门禁的建议。

    Args:
        source_id: 被提炼内容的稳定来源身份。
        workdir: host 工作目录和建议草稿所在目录。
        prompt: 已由调用方构造的完整提炼提示。
        proxy_base_url: 传给默认 CCHost 的代理地址。
        settings_path: 传给默认 CCHost 的 provider settings 路径。
        host_factory: 可选的自定义 host 构造器；提供时其余 host 配置参数不由
            本函数应用。
        date_str: 通过门禁的建议所使用的日期。
        evidence_validator: 可选的 target 证据归属校验函数。
        session_registrar: 传给默认 CCHost 的会话注册器。

    Returns:
        通过门禁的建议和丢弃统计。

    Raises:
        DistillError: host 未发出完成事件、未生成草稿，或草稿未通过结构门禁。
        OSError: 无法创建 MCP 配置或读取建议草稿。
        UnicodeDecodeError: 建议草稿不是有效的 UTF-8 文本。
    """
    if host_factory is not None:
        host = host_factory(source_id, workdir)
    else:
        from trowel_py.cc_host.service import CCHost
        from trowel_py.memory.mcp_config import write_mcp_config

        # 代理与 settings 原样转交；distill kind 使该会话不进入用户候选查询。
        host = CCHost(
            session_id=uuid.uuid4().hex,
            workdir=str(workdir),
            session_kind="distill",
            proxy_base_url=proxy_base_url,
            settings_path=settings_path,
            mcp_config=str(write_mcp_config()),
            session_registrar=session_registrar,
        )

    finished = False
    try:
        async for event in host.send(prompt):
            if getattr(event, "type", None) == "finished":
                finished = True
    finally:
        close = getattr(host, "close", None)
        if close is not None:
            await close()

    if not finished:
        raise DistillError(
            f"distill agent did not finish cleanly for {source_id}"
        )

    draft_path = workdir / _DRAFT_FILE
    if not draft_path.exists():
        raise DistillError(
            f"distill agent produced no {_DRAFT_FILE} for {source_id}"
        )
    return parse_and_gate_draft(
        draft_path.read_text(encoding="utf-8"),
        source_id=source_id,
        date_str=date_str,
        evidence_validator=evidence_validator,
    )
