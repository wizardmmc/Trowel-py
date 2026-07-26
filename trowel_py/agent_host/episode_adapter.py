"""把 StartEpisode 三阶段协议映射到 Session Hub。"""

from __future__ import annotations

import os
import signal
from collections.abc import AsyncIterator
from typing import Any, Callable

from trowel_py.agent_host.binding import RuntimeIdentity
from trowel_py.agent_host.hub import SessionHub
from trowel_py.agent_host.schemas import CreateAgentSessionRequest
from trowel_py.model_os.episode_starting.models import (
    NativeSessionIdentity,
    StartEpisodeCommand,
)
from trowel_py.model_os.types import Episode, MemoryEligibility
from trowel_py.model_os.types import EpisodeRuntimeBinding


def _model_identity(identity: RuntimeIdentity | NativeSessionIdentity) -> NativeSessionIdentity:
    return NativeSessionIdentity(
        agent_session_id=identity.agent_session_id,
        runtime=identity.runtime,
        native_session_id=identity.native_session_id,
        runtime_generation=identity.runtime_generation,
        runtime_pid=identity.runtime_pid,
        runtime_pgid=identity.runtime_pgid,
    )


class AgentEpisodeRuntimeAdapter:
    def __init__(
        self,
        hub: SessionHub,
        *,
        cc_reaper: "CcOrphanReaper | None" = None,
        pid_alive: Callable[[int], bool] | None = None,
    ) -> None:
        self._hub = hub
        self._cc_reaper = cc_reaper or CcOrphanReaper()
        self._pid_alive = pid_alive or _pid_alive

    async def start_native(
        self, command: StartEpisodeCommand, episode: Episode
    ) -> NativeSessionIdentity:
        del episode
        binding = self._hub.create(
            CreateAgentSessionRequest(
                runtime=command.runtime,
                workdir=command.workdir,
                resume_from=None,
                model=command.model,
                effort=command.effort,
                permission_mode=(
                    command.permission if command.runtime == "claude_code" else None
                ),
                permission_preset=(
                    command.permission if command.runtime == "codex" else None
                ),
                memory_enabled=command.memory_enabled,
                profile_enabled=command.profile_enabled,
                self_enabled=True,
                session_kind="user",
                session_purpose=command.session_purpose.value,
                memory_eligibility=(
                    command.memory_eligibility is not MemoryEligibility.INELIGIBLE
                ),
                memory_eligibility_mode=command.memory_eligibility.value,
                agent_mcp_enabled=True,
                model_os_mcp_enabled=True,
            )
        )
        return _model_identity(await self._hub.start_native(binding.session_id))

    async def persist_binding(
        self, identity: NativeSessionIdentity
    ) -> NativeSessionIdentity | None:
        try:
            self._hub.persist_native_identity(identity)  # type: ignore[arg-type]
            return identity
        except Exception:
            if identity.runtime == "claude_code":
                self._cc_reaper.reap(identity)
            elif identity.runtime_pid is not None and self._pid_alive(
                identity.runtime_pid
            ):
                raise RuntimeError(
                    "old Codex app-server is still alive; refusing blind recreate"
                )
            recreated = await self._hub.recreate_managed_session(
                identity.agent_session_id
            )
            return _model_identity(recreated)

    async def refresh_identity(
        self, identity: NativeSessionIdentity
    ) -> NativeSessionIdentity:
        return _model_identity(self._hub.native_identity(identity.agent_session_id))

    async def start_first_turn(
        self, identity: NativeSessionIdentity, text: str
    ) -> AsyncIterator[dict[str, Any]]:
        async for event in self._hub.stream(identity.agent_session_id, text):
            yield event

    def reconcile(self, binding: EpisodeRuntimeBinding) -> str:
        """清理旧 controller 的 runtime，不猜 terminal。"""

        identity = NativeSessionIdentity(
            agent_session_id=binding.agent_session_id,
            runtime=binding.runtime,
            native_session_id=binding.native_session_id,
            runtime_generation=binding.runtime_generation,
            runtime_pid=binding.runtime_pid,
            runtime_pgid=binding.runtime_pgid,
        )
        if binding.runtime == "claude_code":
            try:
                return self._cc_reaper.reap(identity)
            except RuntimeError:
                return "unknown_requires_reconcile"
        if binding.runtime == "codex":
            if binding.runtime_pid is None or not self._pid_alive(binding.runtime_pid):
                return "already_exited"
            return "unknown_requires_reconcile"
        return "unknown_requires_reconcile"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class CcOrphanReaper:
    """只按 durable PID/PGID 清理 controller crash 留下的私有进程组。"""

    def __init__(
        self,
        *,
        getpgid: Callable[[int], int] = os.getpgid,
        getpgrp: Callable[[], int] = os.getpgrp,
        killpg: Callable[[int, int], None] = os.killpg,
        pid_alive: Callable[[int], bool] = _pid_alive,
    ) -> None:
        self._getpgid = getpgid
        self._getpgrp = getpgrp
        self._killpg = killpg
        self._pid_alive = pid_alive

    def reap(self, identity: NativeSessionIdentity) -> str:
        pid = identity.runtime_pid
        pgid = identity.runtime_pgid
        if pid is None or pgid is None:
            raise RuntimeError("CC orphan identity is incomplete")
        if not self._pid_alive(pid):
            return "already_exited"
        try:
            observed_pgid = self._getpgid(pid)
        except ProcessLookupError:
            return "already_exited"
        if observed_pgid != pgid or pid != pgid:
            raise RuntimeError("CC orphan PID/PGID no longer matches durable identity")
        if pgid == self._getpgrp():
            raise RuntimeError("refusing to signal the Trowel process group")
        self._killpg(pgid, signal.SIGKILL)
        return "killed_private_process_group"
