import { useState } from "react";
import { useShallow } from "zustand/react/shallow";

import {
  useCcStore,
  type Task,
} from "../../stores/ccStore";
import { CodexWorkRail } from "./CodexWorkRail";

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

interface TodoBarProps {
  readonly drawerOpen?: boolean;
  readonly onCloseDrawer?: () => void;
}

export function TodoBar({ drawerOpen = false, onCloseDrawer }: TodoBarProps) {
  const active = useCcStore(
    useShallow((state) => {
      const session = state.activeSid
        ? state.sessions[state.activeSid] ?? null
        : null;
      return session
        ? {
            runtime: session.runtime,
            goal: session.goal,
            plan: session.plan,
            tasks: session.tasks,
          }
        : null;
    }),
  );
  const activeSid = useCcStore((state) => state.activeSid);
  const setCodexGoal = useCcStore((state) => state.setCodexGoal);
  const clearCodexGoal = useCcStore((state) => state.clearCodexGoal);
  const tasks = active?.tasks ?? [];
  const [showCompleted, setShowCompleted] = useState(false);

  if (active?.runtime === "codex") {
    return (
      <CodexWorkRail
        key={activeSid}
        goal={active.goal}
        plan={active.plan}
        drawerOpen={drawerOpen}
        onCloseDrawer={onCloseDrawer}
        onSetGoal={(update) => void setCodexGoal(update)}
        onClearGoal={() => void clearCodexGoal()}
      />
    );
  }

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
