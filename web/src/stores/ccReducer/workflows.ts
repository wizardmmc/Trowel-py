import type { WorkflowTreeEvent } from "../../api/ccTypes";
import type { ReducerState, Turn, WorkflowItem } from "./model";

function workflowItemFromEvent(event: WorkflowTreeEvent): WorkflowItem {
  return {
    kind: "workflow",
    runId: event.run_id,
    taskId: event.task_id,
    name: event.name,
    args: event.args,
    status: event.status,
    agentCount: event.agent_count,
    doneCount: event.done_count,
    totalTokens: event.total_tokens,
    totalToolCalls: event.total_tool_calls,
    durationMs: event.duration_ms,
    phases: event.phases,
    agents: event.agents,
    error: event.error,
  };
}

/** 按 runId 更新 workflow 的启动 turn；首次快照写入当前 turn。 */
export function applyWorkflowTree(
  prev: ReducerState,
  event: WorkflowTreeEvent,
): ReducerState {
  const turns = prev.turns;
  if (turns.length === 0) return prev;

  const workflow = workflowItemFromEvent(event);
  let found = false;
  const updatedTurns = turns.map((turn) => {
    if (found) return turn;

    let matched = false;
    const items = turn.items.map((item) => {
      if (item.kind === "workflow" && item.runId === workflow.runId) {
        matched = true;
        return workflow;
      }
      return item;
    });
    if (!matched) return turn;

    found = true;
    return { ...turn, items };
  });
  if (found) return { ...prev, turns: updatedTurns };

  const last = turns[turns.length - 1];
  const updated: Turn = {
    ...last,
    items: [...last.items, workflow],
  };
  return { ...prev, turns: [...turns.slice(0, -1), updated] };
}
