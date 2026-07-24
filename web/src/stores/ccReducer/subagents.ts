import type { SubagentProgressEvent } from "../../api/ccTypes";
import type {
  ReducerState,
  SubagentState,
  ToolItem,
  Turn,
  TurnItem,
} from "./model";

function mergeSubagent(
  prev: SubagentState | undefined,
  event: SubagentProgressEvent,
): SubagentState {
  return {
    status: event.status,
    description: event.description ?? prev?.description ?? null,
    subagent_type: event.subagent_type ?? prev?.subagent_type ?? null,
    last_tool_name: event.last_tool_name ?? prev?.last_tool_name ?? null,
    usage: event.usage ?? prev?.usage ?? null,
  };
}

/** 只把 subagent 进度合并到匹配的 Agent 工具。 */
function mergeSubagentIntoTree(
  items: readonly TurnItem[],
  toolUseId: string,
  event: SubagentProgressEvent,
): readonly TurnItem[] | null {
  let found = false;
  const merge = (tool: ToolItem): ToolItem => {
    if (tool.toolName === "Agent" && tool.toolUseId === toolUseId) {
      found = true;
      return { ...tool, subagent: mergeSubagent(tool.subagent, event) };
    }
    if (tool.childTools.length === 0) {
      return tool;
    }
    return { ...tool, childTools: tool.childTools.map(merge) };
  };

  const result = items.map((item) =>
    item.kind === "tool" ? merge(item) : item,
  );
  return found ? result : null;
}

export function applySubagentProgress(
  prev: ReducerState,
  event: SubagentProgressEvent,
): ReducerState {
  if (!event.tool_use_id) return prev;

  const turns = prev.turns;
  if (turns.length === 0) return prev;

  const last = turns[turns.length - 1];
  const items = mergeSubagentIntoTree(last.items, event.tool_use_id, event);
  if (items !== null) {
    const updated: Turn = { ...last, items };
    return { ...prev, turns: [...turns.slice(0, -1), updated] };
  }

  // workflow agent 已由 WorkflowTree 展示，不能再降级成 standalone 行。
  if (turns.some((turn) => turn.items.some((item) => item.kind === "workflow"))) {
    return prev;
  }

  const standalone = {
    kind: "subagent" as const,
    toolUseId: event.tool_use_id,
    subagent: mergeSubagent(undefined, event),
  };
  const updated: Turn = { ...last, items: [...last.items, standalone] };
  return { ...prev, turns: [...turns.slice(0, -1), updated] };
}
