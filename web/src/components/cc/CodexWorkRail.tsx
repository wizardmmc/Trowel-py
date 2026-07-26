import { useState } from "react";

import type {
  CodexGoal,
  CodexPlan,
  CodexPlanStep,
} from "../../stores/ccStore";

interface CodexWorkRailProps {
  readonly goal: CodexGoal | null;
  readonly plan: CodexPlan | null;
  readonly drawerOpen: boolean;
  readonly onCloseDrawer?: () => void;
  readonly onSetGoal: (update: {
    readonly objective?: string;
    readonly status?: CodexGoal["status"];
    readonly token_budget?: number | null;
  }) => void;
  readonly onClearGoal: () => void;
}

export function CodexWorkRail({
  goal,
  plan,
  drawerOpen,
  onCloseDrawer,
  onSetGoal,
  onClearGoal,
}: CodexWorkRailProps) {
  const [editing, setEditing] = useState(false);
  const [objective, setObjective] = useState(goal?.objective ?? "");
  const [tokenBudget, setTokenBudget] = useState(
    goal?.tokenBudget === null || goal?.tokenBudget === undefined
      ? ""
      : String(goal.tokenBudget),
  );

  function beginEditing() {
    setObjective(goal?.objective ?? "");
    setTokenBudget(
      goal?.tokenBudget === null || goal?.tokenBudget === undefined
        ? ""
        : String(goal.tokenBudget),
    );
    setEditing(true);
  }

  const completed =
    plan?.steps.filter((step) => step.status === "completed").length ?? 0;
  const canSave =
    objective.trim().length > 0 &&
    (tokenBudget === "" || Number(tokenBudget) > 0);

  function saveGoal() {
    if (!canSave) return;
    onSetGoal({
      objective: objective.trim(),
      token_budget: tokenBudget === "" ? null : Number(tokenBudget),
    });
    setEditing(false);
  }

  return (
    <aside
      className={`cc-todobar cc-workrail${drawerOpen ? " cc-workrail--open" : ""}`}
      aria-label="Codex 目标与计划"
    >
      <div className="cc-todobar__head cc-workrail__head">
        <span>目标与计划</span>
        <span className="cc-workrail__runtime">Codex</span>
        <button
          type="button"
          className="cc-workrail__close"
          aria-label="关闭目标与计划"
          title="关闭"
          onClick={onCloseDrawer}
        >
          ×
        </button>
      </div>
      <div className="cc-workrail__scroll">
        <section
          className="cc-workrail__section"
          aria-labelledby="codex-goal-title"
        >
          <div className="cc-workrail__section-head">
            <span id="codex-goal-title" className="cc-workrail__section-title">
              Goal
            </span>
            {goal && (
              <span className={`cc-goal-status cc-goal-status--${goal.status}`}>
                {goalStatusLabel(goal.status)}
              </span>
            )}
            {goal && !editing && (
              <span className="cc-goal-actions">
                {goal.status !== "complete" && (
                  <button
                    type="button"
                    className="cc-goal-action"
                    aria-label={
                      goal.status === "active" ? "暂停 Goal" : "恢复 Goal"
                    }
                    title={goal.status === "active" ? "暂停 Goal" : "恢复 Goal"}
                    onClick={() =>
                      onSetGoal({
                        status:
                          goal.status === "active" ? "paused" : "active",
                      })
                    }
                  >
                    {goal.status === "active" ? "Ⅱ" : "▶"}
                  </button>
                )}
                <button
                  type="button"
                  className="cc-goal-action"
                  aria-label="编辑 Goal"
                  title="编辑 Goal"
                  onClick={beginEditing}
                >
                  ✎
                </button>
                <button
                  type="button"
                  className="cc-goal-action"
                  aria-label="清除 Goal"
                  title="清除 Goal"
                  onClick={onClearGoal}
                >
                  ×
                </button>
              </span>
            )}
          </div>
          {editing ? (
            <div className="cc-goal-editor">
              <textarea
                value={objective}
                onChange={(event) => setObjective(event.target.value)}
                aria-label="Goal 目标"
                rows={4}
                autoFocus
              />
              <label>
                <span>Token budget</span>
                <input
                  type="number"
                  min="1"
                  value={tokenBudget}
                  onChange={(event) => setTokenBudget(event.target.value)}
                  placeholder="不限制"
                />
              </label>
              <div className="cc-goal-editor__actions">
                <button type="button" onClick={() => setEditing(false)}>
                  取消
                </button>
                <button type="button" disabled={!canSave} onClick={saveGoal}>
                  保存
                </button>
              </div>
            </div>
          ) : goal ? (
            <>
              <p className="cc-goal-objective">{goal.objective}</p>
              <GoalUsage goal={goal} />
            </>
          ) : (
            <div className="cc-workrail__empty">
              <span>当前 thread 没有 Goal</span>
              <button type="button" onClick={beginEditing}>
                设置 Goal
              </button>
            </div>
          )}
        </section>
        <section
          className="cc-workrail__section"
          aria-labelledby="codex-plan-title"
        >
          <div className="cc-workrail__section-head">
            <span id="codex-plan-title" className="cc-workrail__section-title">
              Plan
            </span>
            <span className="cc-workrail__meta">
              {completed} / {plan?.steps.length ?? 0}
            </span>
          </div>
          {plan?.explanation && (
            <p className="cc-plan-explanation">{plan.explanation}</p>
          )}
          {plan && plan.steps.length > 0 ? (
            <div className="cc-plan-list">
              {plan.steps.map((step, index) => (
                <PlanItem key={`${index}-${step.step}`} step={step} />
              ))}
            </div>
          ) : (
            <div className="cc-workrail__empty">当前 turn 暂无 Plan</div>
          )}
        </section>
      </div>
    </aside>
  );
}

function GoalUsage({ goal }: { readonly goal: CodexGoal }) {
  const percent =
    goal.tokenBudget && goal.tokenBudget > 0
      ? Math.min(100, Math.round((goal.tokensUsed / goal.tokenBudget) * 100))
      : null;
  return (
    <div className="cc-goal-usage">
      <div className="cc-goal-usage__row">
        <span>tokens</span>
        <span>
          {formatCount(goal.tokensUsed)}
          {goal.tokenBudget === null ? "" : ` / ${formatCount(goal.tokenBudget)}`}
        </span>
      </div>
      {percent !== null && (
        <div
          className="cc-goal-usage__bar"
          aria-label={`Goal token budget 已使用 ${percent}%`}
        >
          <span style={{ width: `${percent}%` }} />
        </div>
      )}
      <div className="cc-goal-usage__time">
        elapsed {formatDuration(goal.timeUsedSeconds)}
      </div>
    </div>
  );
}

function PlanItem({ step }: { readonly step: CodexPlanStep }) {
  const mark =
    step.status === "completed"
      ? "✓"
      : step.status === "inProgress"
        ? "◐"
        : "○";
  const label = step.status === "inProgress" ? "in progress" : step.status;
  return (
    <div className={`cc-plan-item cc-plan-item--${step.status}`}>
      <span className="cc-plan-item__mark" aria-hidden="true">
        {mark}
      </span>
      <div>
        <div className="cc-plan-item__text">{step.step}</div>
        <div className="cc-plan-item__state">{label}</div>
      </div>
    </div>
  );
}

function goalStatusLabel(status: CodexGoal["status"]): string {
  return {
    active: "运行中",
    paused: "已暂停",
    blocked: "受阻",
    usageLimited: "额度受限",
    budgetLimited: "预算用尽",
    complete: "已完成",
  }[status];
}

function formatCount(value: number): string {
  return value >= 1000
    ? `${(value / 1000).toFixed(1).replace(".0", "")}k`
    : String(value);
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return seconds % 60 === 0
    ? `${minutes}m`
    : `${minutes}m ${seconds % 60}s`;
}
