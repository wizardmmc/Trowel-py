import { useState } from "react";

import { useActiveSession, type Task } from "../../stores/ccStore";

/**
 * slice-028 D1 TodoBar — renders the active session's V2 task list
 * (TaskCreate/TaskUpdate, maintained in ccStore).
 *
 * Layout B1 (mockup): flat list ordered by taskId; in-progress surfaced,
 * completed collapse to the bottom behind a "已完成 N 项" toggle. Scope is
 * session-level (cross-turn) — switching sessions swaps the list (each
 * PerSessionState carries its own tasks).
 *
 * Renders an idle hint when there is no active session or no tasks yet, so the
 * column is never an empty white pane.
 */
function statusIcon(status: Task["status"]): string {
  switch (status) {
    case "in_progress":
      return "◐";
    case "completed":
      return "✓";
    default:
      return "○";
  }
}

export function TodoBar() {
  const active = useActiveSession();
  const tasks = active?.tasks ?? [];
  const [showCompleted, setShowCompleted] = useState(false);

  const active0rPending = tasks.filter((t) => t.status !== "completed");
  const completed = tasks.filter((t) => t.status === "completed");
  const doneCount = completed.length;

  return (
    <aside className="cc-todobar" aria-label="任务列表">
      <div className="cc-todobar__head">
        <span>待办</span>
        <span className="cc-todobar__progress">
          {doneCount}/{tasks.length || 0}
        </span>
      </div>
      <div className="cc-todobar__list">
        {tasks.length === 0 && (
          <div className="cc-todobar__empty">
            {active ? "本 session 暂无任务" : "未选择 session"}
          </div>
        )}
        {active0rPending.map((t) => (
          <TodoItem key={t.toolUseId} task={t} />
        ))}
        {completed.length > 0 && (
          <button
            type="button"
            className="cc-todobar__collapsed"
            onClick={() => setShowCompleted((v) => !v)}
            aria-expanded={showCompleted}
          >
            {showCompleted ? "▴" : "▾"} 已完成 {completed.length} 项
          </button>
        )}
        {showCompleted &&
          completed.map((t) => <TodoItem key={t.toolUseId} task={t} />)}
      </div>
    </aside>
  );
}

function TodoItem({ task }: { readonly task: Task }) {
  const doing = task.status === "in_progress";
  const done = task.status === "completed";
  return (
    <div
      className={
        "cc-todobar__item" +
        (doing ? " cc-todobar__item--doing" : "") +
        (done ? " cc-todobar__item--done" : "")
      }
    >
      <span
        className={
          "cc-todobar__icon" +
          (doing ? " cc-todobar__icon--doing" : "") +
          (done ? " cc-todobar__icon--done" : "")
        }
        aria-hidden="true"
      >
        {statusIcon(task.status)}
      </span>
      <div className="cc-todobar__body">
        <div className="cc-todobar__subject">{task.subject}</div>
        {doing && task.activeForm && (
          <div className="cc-todobar__active">{task.activeForm}</div>
        )}
      </div>
    </div>
  );
}
