"""slice-077-prefix: logical-turn background activity tracker.

CC emits a top-level ``result`` event at TWO different boundaries:

1. **native segment end** — CC finished the current front-ground reasoning,
   but a background task (Bash / Agent) or workflow may still be running, and
   CC will auto-continue once that activity completes.
2. **logical turn end** — no pending background activity remains; CC is done
   with this user message.

trowel must treat only (2) as the SSE / running / watermark terminal. (1) is a
mid-turn boundary: reset the native segment accumulator, keep reading stdout,
keep ``running=True``, do not advance the completed offset.

This module owns the state that distinguishes (1) from (2): the set of task_ids
CC started in the background but has not yet reported a recorded terminal
signal. Usually that signal is ``task_notification``. CC 2.1.197 also emits
``task_updated.patch.status=completed`` without a notification when the
assistant waits through ``TaskOutput(block=true)``. Workflow in-flight state
stays on the existing ``WorkflowWatcher`` (slice-036); this tracker only covers
task_* events.

Ground truth for the task_* signals: slice-077-prefix 隔离复现 C, translator
sample 030, and ``tests/cc_host/fixtures/bg_taskoutput_completed.jsonl`` (real
CC 2.1.197 recordings). ``task_notification`` terminates regardless of its
status value. The notification-free TaskOutput path terminates only on the
recorded ``task_updated.patch.status=completed`` value; other update statuses
remain pending until they are recorded and specified.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PendingTask:
    """One CC background task tracked from ``task_started`` to terminal.

    Attributes:
        task_id: the stable CC task identity (never reuse command text or event
            order in its place).
        tool_use_id: the tool_use that launched this task; used to attach back
            to the originating ToolItem.
        task_type: CC's ``task_type`` (e.g. ``local_bash`` / ``local_agent``);
            carried for diagnosis/UI, not used for termination logic.
        last_status: the most recent non-terminal status while the task was
            pending (``started`` / ``progress``). The entry is removed when the
            service validates a recorded terminal signal.
    """

    task_id: str
    tool_use_id: str
    task_type: str | None
    last_status: str


class BackgroundActivityTracker:
    """Tracks live background tasks for one CCHost turn.

    A turn has background activity (and thus a mid-result is NOT terminal) when
    at least one task_id is pending — a ``task_started`` was seen without a
    matching recorded terminal signal. Workflow in-flight state is owned by
    ``WorkflowWatcher`` and OR'd in at the call site.

    Thread-safety: single-owner. CCHost's send() and the disconnect drain share
    one tracker, but send() awaits the drain at its entry, so the two never
    touch the tracker concurrently.
    """

    def __init__(self) -> None:
        """Start with an empty pending set (no background activity)."""

        self._pending: dict[str, PendingTask] = {}

    def register_started(
        self, task_id: str, tool_use_id: str, task_type: str | None
    ) -> None:
        """Register a task_id as pending on ``task_started``.

        Args:
            task_id: CC's task identity. Empty id is ignored (defensive — CC
                always emits one; an empty id would be unmappable).
            tool_use_id: the originating tool_use.
            task_type: CC's ``task_type`` field if present.
        """

        if not task_id:
            return
        self._pending[task_id] = PendingTask(
            task_id=task_id, tool_use_id=tool_use_id,
            task_type=task_type, last_status="started",
        )

    def mark_progress(self, task_id: str) -> None:
        """Note a ``task_progress`` for an already-pending task.

        No-op for an unknown task_id (no task_started was seen — do not invent
        identity; slice-077-prefix 失败测试 4).
        """

        if not task_id:
            return
        cur = self._pending.get(task_id)
        if cur is None:
            return
        self._pending[task_id] = PendingTask(
            task_id=cur.task_id, tool_use_id=cur.tool_use_id,
            task_type=cur.task_type, last_status="progress",
        )

    def terminate(self, task_id: str) -> bool:
        """Remove a pending task after the caller validates a terminal signal.

        The tracker owns identity and idempotency, not raw-event validation.
        The service accepts any ``task_notification`` or the separately
        recorded ``task_updated.patch.status=completed`` path before calling
        this method.

        Idempotent: a duplicate notification for an already-removed task
        returns False (slice-077-prefix 失败测试 4: duplicate terminal
        notification must not double-decrement).

        Returns:
            True if the task was pending and is now removed; False if unknown
            (duplicate or unsolicited notification).
        """

        if not task_id:
            return False
        return self._pending.pop(task_id, None) is not None

    def has_pending_tasks(self) -> bool:
        """Whether any background task is still pending (→ a result is mid-turn)."""

        return bool(self._pending)

    def pending_ids(self) -> frozenset[str]:
        """A snapshot of currently-pending task_ids (defensive copy)."""

        return frozenset(self._pending)

    def reset(self) -> None:
        """Clear all pending tasks (C-9 process-generation isolation).

        Called at each new turn's send entry. No explicit ``process_generation``
        counter: a new turn always starts after the prior stdout reader has
        stopped — either the prior ``send`` returned normally (its loop broke),
        or this ``send``'s entry awaited the disconnect drain to completion. A
        respawn also gives CC a NEW subprocess (new stdout pipe), so old late
        events cannot reach the new turn's reader. Per-turn reset + the
        single-owner pipe invariant already isolate generations; a counter
        would be defence-in-depth without an observable failure mode under the
        current send/drain lockstep.
        """

        self._pending.clear()
