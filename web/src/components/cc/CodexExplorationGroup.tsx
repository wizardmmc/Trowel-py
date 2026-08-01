import { memo, useState } from "react";

import type { ToolItem } from "../../agent/domain";
import {
  getCodexCommandPresentation,
  type CodexCommandRow,
} from "./codexCommandPresentation";
import { ToolCommandOutput } from "./ToolCommandOutput";

interface CodexExplorationGroupProps {
  readonly items: readonly ToolItem[];
  readonly workdir?: string;
}

interface ExplorationAction {
  readonly key: string;
  readonly row: CodexCommandRow;
  readonly status: ToolItem["status"];
  readonly failedItem: ToolItem | null;
}

const VISIBLE_RUNNING_ACTIONS = 4;
const VISIBLE_DONE_ACTIONS = 1;
const ACTION_ORDER: readonly CodexCommandRow["verb"][] = [
  "Read",
  "Search",
  "List",
];

function actionSummary(actions: readonly ExplorationAction[]): string {
  return ACTION_ORDER.flatMap((verb) => {
    const count = actions.filter((action) => action.row.verb === verb).length;
    return count > 0 ? [`${verb} ${count}`] : [];
  }).join(" · ");
}

function ActionState({ status }: { readonly status: ToolItem["status"] }) {
  if (status === "running") {
    return <span className="cc-tool__spinner" aria-label="进行中" />;
  }
  if (status === "failed") {
    return (
      <span className="cc-tool__check cc-tool__check--failed" aria-label="失败">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M6 6l12 12M18 6L6 18" />
        </svg>
      </span>
    );
  }
  return (
    <span className="cc-tool__check" aria-label="完成">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M5 13l4 4L19 7" />
      </svg>
    </span>
  );
}

function ExplorationActionRow({ action }: { readonly action: ExplorationAction }) {
  const failed = action.failedItem;
  return (
    <div
      className="cc-tool cc-exploration__action"
      data-status={action.status}
    >
      <div className="cc-tool__summary cc-tool__summary--condensed">
        <svg className="cc-tool__icon" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="12" cy="12" r="3" />
          <path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2" />
        </svg>
        <span className="cc-tool__name">{action.row.verb}</span>
        <code
          className="cc-tool__brief cc-tool__brief--mono"
          title={action.row.detail}
        >
          {action.row.detail}
        </code>
        {failed && typeof failed.exitCode === "number" && (
          <span className="cc-tool__exit">exit {failed.exitCode}</span>
        )}
        <ActionState status={action.status} />
      </div>
      {failed?.result !== null && failed?.result !== undefined && (
        <div className="cc-tool__detail cc-exploration__failure">
          <ToolCommandOutput item={failed} />
        </div>
      )}
    </div>
  );
}

function CodexExplorationGroupView({ items, workdir }: CodexExplorationGroupProps) {
  const [expanded, setExpanded] = useState(false);
  const running = items.some((item) => item.status === "running");
  const failed = items.filter((item) => item.status === "failed").length;
  const calls = items.length;
  const actions: readonly ExplorationAction[] = items.flatMap((item) => {
    const rows = getCodexCommandPresentation(item, workdir).rows;
    return rows.map((row, index) => ({
      key: `${item.toolUseId}:${index}`,
      row,
      status: item.status,
      failedItem:
        item.status === "failed" && index === rows.length - 1 ? item : null,
    }));
  });
  const state = running ? "running" : failed > 0 ? "failed" : "done";
  const stateLabel = running ? "Running" : failed > 0 ? "Failed" : "Done";
  const meta = `${calls} ${calls === 1 ? "call" : "calls"} · ${actions.length} ${actions.length === 1 ? "action" : "actions"}${failed > 0 ? ` · ${failed} failed` : ""}`;
  const autoCount = running ? VISIBLE_RUNNING_ACTIONS : VISIBLE_DONE_ACTIONS;
  const expandable = actions.length > autoCount;
  const visibleCount = expanded
    ? actions.length
    : Math.min(autoCount, actions.length);
  const visibleActions = actions.slice(-visibleCount);
  const hiddenCount = actions.length - visibleCount;
  const toggle = (): void => setExpanded((current) => !current);
  return (
    <section
      className="cc-exploration cc-subagent"
      data-status={state}
      aria-label={`Explore · ${meta} · ${stateLabel}`}
    >
      <div
        className="cc-subagent__header cc-exploration__header"
        role={expandable ? "button" : undefined}
        tabIndex={expandable ? 0 : undefined}
        aria-expanded={expandable ? expanded : undefined}
        onClick={expandable ? toggle : undefined}
        onKeyDown={
          expandable
            ? (event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  toggle();
                }
              }
            : undefined
        }
      >
        <svg className="cc-subagent__icon" viewBox="0 0 24 24" aria-hidden="true">
          <rect x="4" y="4" width="16" height="16" rx="2" />
          <path d="M9 9h6M9 13h6M9 17h4" />
        </svg>
        <span className="cc-subagent__name">Explore</span>
        <span className="cc-subagent__desc">{actionSummary(actions)}</span>
        <span className="cc-exploration__meta">{meta}</span>
        {running ? (
          <span className="cc-subagent__spin cc-spin-ring" aria-label="进行中" />
        ) : (
          <span
            className={`cc-subagent__done${failed > 0 ? " cc-subagent__done--failed" : ""}`}
          >
            {stateLabel}
          </span>
        )}
      </div>
      <div className="cc-subagent__children cc-exploration__actions">
        {visibleActions.map((action) => (
          <ExplorationActionRow key={action.key} action={action} />
        ))}
        {hiddenCount > 0 && (
          <button
            type="button"
            className="cc-subagent__more"
            onClick={() => setExpanded(true)}
          >
            +{hiddenCount} more
          </button>
        )}
      </div>
    </section>
  );
}

function sameExploration(
  previous: CodexExplorationGroupProps,
  next: CodexExplorationGroupProps,
): boolean {
  return (
    previous.workdir === next.workdir &&
    previous.items.length === next.items.length &&
    previous.items.every((item, index) => item === next.items[index])
  );
}

export const CodexExplorationGroup = memo(
  CodexExplorationGroupView,
  sameExploration,
);
