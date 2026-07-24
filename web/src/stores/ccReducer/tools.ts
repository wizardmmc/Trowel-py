import type {
  ToolCallEvent,
  ToolProgressEvent,
  ToolResultEvent,
} from "../../api/ccTypes";
import type { ReducerState, ToolItem, Turn } from "./model";
import { resolveElicitationResult } from "./requests";
import { applyTaskToolCall, assignTaskIdFromResult } from "./tasks";
import { attachChildToParent, updateToolInCurrentTurn } from "./toolTree";

export function applyToolCall(
  prev: ReducerState,
  event: ToolCallEvent,
): ReducerState {
  const codexInput = event.input as { cwd?: unknown };
  const newItem: ToolItem = {
    kind: "tool",
    toolUseId: event.tool_use_id,
    toolName: event.tool_name,
    input: event.input,
    status: "running",
    elapsedSeconds: null,
    result: null,
    childTools: [],
    cwd: typeof codexInput.cwd === "string" ? codexInput.cwd : null,
  };
  const withTasks = applyTaskToolCall(prev, event);
  const parentId = event.parent_tool_use_id;
  if (parentId) {
    const attached = attachChildToParent(withTasks, parentId, newItem);
    if (attached !== null) return { ...attached, phase: "tool" };
  }
  return appendTool(withTasks, newItem);
}

export function applyToolProgress(
  prev: ReducerState,
  event: ToolProgressEvent,
): ReducerState {
  return {
    ...updateToolInCurrentTurn(prev, event.tool_use_id, (tool) => ({
      ...tool,
      elapsedSeconds: event.elapsed_time_seconds,
    })),
    phase: "tool",
  };
}

export function applyToolResult(
  prev: ReducerState,
  event: ToolResultEvent,
): ReducerState {
  const withElicitation = resolveElicitationResult(
    prev,
    event.tool_use_id,
    event.content,
  );
  if (withElicitation !== null) {
    return { ...withElicitation, phase: "tool" };
  }

  const afterTask = assignTaskIdFromResult(
    prev,
    event.tool_use_id,
    event.content ?? "",
  );
  return {
    ...updateToolInCurrentTurn(afterTask, event.tool_use_id, (tool) => ({
      ...tool,
      status: toolResultStatus(event, tool),
      result: event.content,
      writeDiff: event.write_diff ?? tool.writeDiff,
      exitCode: event.exit_code ?? tool.exitCode,
      durationMs: event.duration_ms ?? tool.durationMs,
      cwd: event.cwd ?? tool.cwd,
      nativeStatus: event.status ?? tool.nativeStatus,
    })),
    phase: "tool",
  };
}

function appendTool(prev: ReducerState, item: ToolItem): ReducerState {
  const turns = prev.turns;
  if (turns.length === 0) return { ...prev, phase: "tool" };

  const last = turns[turns.length - 1];
  const updatedLast: Turn = {
    ...last,
    items: [...last.items, item],
  };
  return {
    ...prev,
    phase: "tool",
    turns: [...turns.slice(0, -1), updatedLast],
  };
}

/** Codex 的失败/拒绝/非零退出必须显示失败；CC 无这些字段时仍为完成。 */
function toolResultStatus(
  event: ToolResultEvent,
  tool: ToolItem,
): "done" | "failed" {
  const nativeStatus = event.status ?? tool.nativeStatus;
  if (nativeStatus === "failed" || nativeStatus === "declined") {
    return "failed";
  }
  const exitCode = event.exit_code ?? tool.exitCode;
  if (typeof exitCode === "number" && exitCode !== 0) {
    return "failed";
  }
  return "done";
}
