import type { ToolCallEvent } from "../../api/ccTypes";
import type { ReducerState, Task } from "./model";

const TASK_CREATED_RE = /Task\s+#(\d+)\s+created/i;

/** 根据 TaskCreate/TaskUpdate 工具调用维护 session 级任务列表。 */
export function applyTaskToolCall(
  prev: ReducerState,
  event: ToolCallEvent,
): ReducerState {
  if (event.tool_name === "TaskCreate") {
    const input = event.input as {
      subject?: unknown;
      description?: unknown;
      activeForm?: unknown;
    };
    const task: Task = {
      taskId: null,
      toolUseId: event.tool_use_id,
      subject: typeof input.subject === "string" ? input.subject : "",
      description:
        typeof input.description === "string" ? input.description : undefined,
      activeForm:
        typeof input.activeForm === "string" ? input.activeForm : undefined,
      status: "pending",
    };
    return { ...prev, tasks: [...prev.tasks, task] };
  }

  if (event.tool_name !== "TaskUpdate") {
    return prev;
  }

  const input = event.input as { taskId?: unknown; status?: unknown };
  if (typeof input.taskId !== "string") return prev;
  if (input.status !== "in_progress" && input.status !== "completed") {
    return prev;
  }

  const status: Task["status"] = input.status;
  let found = false;
  const tasks = prev.tasks.map((task) => {
    if (task.taskId !== input.taskId) {
      return task;
    }
    found = true;
    return { ...task, status };
  });
  return found ? { ...prev, tasks } : prev;
}

/** 从 TaskCreate 结果中提取服务端分配的 task id。 */
export function assignTaskIdFromResult(
  prev: ReducerState,
  toolUseId: string,
  content: string,
): ReducerState {
  const match = content.match(TASK_CREATED_RE);
  if (!match) return prev;

  const taskId = match[1];
  let found = false;
  const tasks = prev.tasks.map((task) => {
    if (task.toolUseId !== toolUseId || task.taskId !== null) {
      return task;
    }
    found = true;
    return { ...task, taskId };
  });
  return found ? { ...prev, tasks } : prev;
}
