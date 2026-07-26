"""controller 启动时先核对旧 runtime，再关闭已失效的 pending channel。"""

from __future__ import annotations

from typing import Protocol

from trowel_py.model_os.types import (
    EpisodeRuntimeBinding,
    EpisodeStatus,
    ReconcileReason,
)


class RuntimeReconciler(Protocol):
    def reconcile(self, binding: EpisodeRuntimeBinding) -> str: ...


class StartupReconciler:
    def __init__(self, store, *, runtime_reconciler: RuntimeReconciler) -> None:
        self._store = store
        self._runtime = runtime_reconciler

    def run(self) -> tuple[tuple[str, str], ...]:
        snapshot = self._store.read_snapshot()
        results: list[tuple[str, str]] = []
        for episode in snapshot.episodes:
            if episode.status not in {
                EpisodeStatus.SUSPENDED_WAITING_INPUT,
                EpisodeStatus.SUSPENDED_WAITING_APPROVAL,
                EpisodeStatus.SUSPENDED_READY,
            }:
                continue
            binding = self._store.episode_runtime_binding(episode.episode_id)
            if binding is None:
                outcome = "missing_runtime_identity"
            else:
                try:
                    outcome = self._runtime.reconcile(binding)
                except Exception:
                    outcome = "unknown_requires_reconcile"
            current = self._store.read_snapshot().episode_by_id(episode.episode_id)
            if current is not None and current.status in {
                EpisodeStatus.SUSPENDED_WAITING_INPUT,
                EpisodeStatus.SUSPENDED_WAITING_APPROVAL,
                EpisodeStatus.SUSPENDED_READY,
            }:
                self._store.mark_pending_channel_lost(
                    episode.episode_id,
                    reason=ReconcileReason.REQUIRES_USER_RESTART,
                )
            results.append((episode.episode_id, outcome))
        return tuple(results)
