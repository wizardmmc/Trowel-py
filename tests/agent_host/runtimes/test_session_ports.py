from __future__ import annotations

from typing import Any

import pytest

from trowel_py.agent_host.binding import Runtime, SessionBinding, make_binding
from trowel_py.agent_host.runtimes.base import RuntimeCloseResult
from trowel_py.agent_host.runtimes.claude_code import ClaudeCodeRuntimeAdapter
from trowel_py.agent_host.runtimes.codex import CodexRuntimeAdapter


class _CcHost:
    """提供 Claude Code 运行时接口要求的实时状态。"""

    is_dead = False
    has_in_flight_turn = True


class _CodexSession:
    """提供 Codex 运行时接口要求的实时状态。"""

    has_in_flight_turn = True


class _CodexManager:
    """记录 Codex adapter 的查找和关闭操作。"""

    def __init__(self) -> None:
        self.sessions = {"codex-1": _CodexSession()}
        self.close_calls: list[tuple[str, bool]] = []

    @property
    def session_ids(self) -> tuple[str, ...]:
        """返回当前登记的 Codex 会话。"""

        return tuple(self.sessions)

    def get_session(self, session_id: str) -> Any | None:
        """按 Trowel 会话 ID 返回 Codex 会话。"""

        return self.sessions.get(session_id)

    def unregister(self, session_id: str) -> Any | None:
        """注销一个 Codex 会话。"""

        return self.sessions.pop(session_id, None)

    async def close_session(
        self,
        session: _CodexSession,
        *,
        preserve_history: bool,
        terminal_timeout_s: float = 2.0,
    ) -> None:
        """记录 adapter 选择的原生 thread 收敛语义。"""

        del terminal_timeout_s
        session_id = next(
            key for key, candidate in self.sessions.items() if candidate is session
        )
        self.close_calls.append((session_id, preserve_history))


def _binding(
    session_id: str,
    runtime: Runtime,
    *,
    session_kind: str = "user",
) -> SessionBinding:
    """创建 runtime adapter 关闭测试所需的最小 binding。"""

    return make_binding(
        session_id=session_id,
        runtime=runtime,
        native_session_id=None,
        workdir="/tmp/work",
        model=None,
        effort=None,
        permission=None,
        memory_enabled=True,
        profile_enabled=True,
        capabilities=(),
        name="work",
        session_kind=session_kind,
    )


@pytest.mark.anyio
async def test_runtime_adapters_expose_the_same_live_session_contract() -> None:
    cc_registry = {"cc-1": _CcHost()}
    closed: list[str] = []

    async def close_cc(session_id: str, registry: dict[str, Any]) -> None:
        """模拟 Claude Code 关闭后从 registry 移除会话。"""

        closed.append(session_id)
        registry.pop(session_id)

    cc = ClaudeCodeRuntimeAdapter(cc_registry, closer=close_cc)
    codex_manager = _CodexManager()
    codex = CodexRuntimeAdapter(codex_manager)

    assert cc.runtime is Runtime.CLAUDE_CODE
    assert codex.runtime is Runtime.CODEX
    assert cc.session_ids() == ("cc-1",)
    assert codex.session_ids() == ("codex-1",)
    assert cc.live_state("cc-1").connected is True
    assert cc.live_state("cc-1").has_in_flight_turn is True
    assert codex.live_state("codex-1").connected is True
    assert codex.live_state("codex-1").has_in_flight_turn is True

    cc_result = await cc.close(_binding("cc-1", Runtime.CLAUDE_CODE))
    codex_result = await codex.close(_binding("codex-1", Runtime.CODEX))

    assert closed == ["cc-1"]
    assert cc_result == RuntimeCloseResult.closed()
    assert codex_result == RuntimeCloseResult.closed()
    assert codex_manager.close_calls == [("codex-1", True)]
    assert cc.session_ids() == ()
    assert codex.session_ids() == ()


def test_dead_cc_process_keeps_registered_session_connected() -> None:
    """中断只结束当前进程；仍在 registry 的会话可由下一条消息原生恢复。"""

    host = _CcHost()
    host.is_dead = True
    host.has_in_flight_turn = False

    state = ClaudeCodeRuntimeAdapter({"cc-1": host}).live_state("cc-1")

    assert state.connected is True
    assert state.has_in_flight_turn is False


@pytest.mark.anyio
async def test_codex_internal_session_stays_archived_on_close() -> None:
    manager = _CodexManager()
    adapter = CodexRuntimeAdapter(manager)

    result = await adapter.close(
        _binding("codex-1", Runtime.CODEX, session_kind="delegate")
    )

    assert result == RuntimeCloseResult.closed()
    assert manager.close_calls == [("codex-1", False)]


@pytest.mark.anyio
async def test_runtime_close_failures_return_reconcile_without_unregistering() -> None:
    """已知资源收敛失败应保留实时登记，并返回调用方可重试的结果。"""

    cc_registry = {"cc-1": _CcHost()}

    async def fail_cc_close(_session_id: str, _registry: dict[str, Any]) -> None:
        """模拟 CC 进程组在强制结束后仍存活。"""

        raise RuntimeError("process group survived")

    class FailingCodexManager(_CodexManager):
        """模拟 Codex archive 或 unarchive 的预期关闭失败。"""

        async def close_session(self, *_args, **_kwargs) -> None:
            """保留 manager 登记并报告需要后续收敛。"""

            raise RuntimeError("native close needs reconciliation")

    cc = ClaudeCodeRuntimeAdapter(cc_registry, closer=fail_cc_close)
    codex_manager = FailingCodexManager()
    codex = CodexRuntimeAdapter(codex_manager)

    cc_result = await cc.close(_binding("cc-1", Runtime.CLAUDE_CODE))
    codex_result = await codex.close(_binding("codex-1", Runtime.CODEX))

    assert cc_result.status == "needs_reconcile"
    assert cc_result.remaining_resource_kinds == ("claude_code_process_group",)
    assert codex_result.status == "needs_reconcile"
    assert codex_result.remaining_resource_kinds == ("codex_session_close",)
    assert cc.session_ids() == ("cc-1",)
    assert codex.session_ids() == ("codex-1",)


def test_claude_code_adapter_requires_the_explicit_in_flight_contract() -> None:
    class _LegacyHost:
        """只提供旧 running 字段，不满足新的运行时接口。"""

        is_dead = False
        running = True

    adapter = ClaudeCodeRuntimeAdapter({"legacy": _LegacyHost()})

    with pytest.raises(AttributeError, match="has_in_flight_turn"):
        adapter.live_state("legacy")
