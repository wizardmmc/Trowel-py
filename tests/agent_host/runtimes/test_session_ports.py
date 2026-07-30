from __future__ import annotations

from typing import Any

import pytest

from trowel_py.agent_host.binding import Runtime
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

    await cc.close("cc-1")
    await codex.close("codex-1")

    assert closed == ["cc-1"]
    assert cc.session_ids() == ()
    assert codex.session_ids() == ()


def test_claude_code_adapter_requires_the_explicit_in_flight_contract() -> None:
    class _LegacyHost:
        """只提供旧 running 字段，不满足新的运行时接口。"""

        is_dead = False
        running = True

    adapter = ClaudeCodeRuntimeAdapter({"legacy": _LegacyHost()})

    with pytest.raises(AttributeError, match="has_in_flight_turn"):
        adapter.live_state("legacy")
