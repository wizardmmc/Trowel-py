/** 创建和查询五项后台任务的完整本地状态。 */

import type {
  ConfigurationCatalog,
  TaskBinding,
  TaskId,
} from "../domain/types";
import { TASKS } from "../domain/types";

export type TaskValues = Readonly<Record<TaskId, string | null>>;
export type TaskFlags = Readonly<Record<TaskId, boolean>>;
export type TaskErrors = Readonly<Record<TaskId, string | null>>;

/** 用稳定任务集合建立完整记录，避免遗漏未绑定项。 */
export function emptyTaskValues(): TaskValues {
  return Object.fromEntries(TASKS.map((task) => [task.id, null])) as Record<
    TaskId,
    null
  >;
}

/** 用稳定任务集合建立保存或脏状态记录。 */
export function emptyTaskFlags(value: boolean): TaskFlags {
  return Object.fromEntries(TASKS.map((task) => [task.id, value])) as Record<
    TaskId,
    boolean
  >;
}

/** 用稳定任务集合建立行级错误记录。 */
export function emptyTaskErrors(): TaskErrors {
  return Object.fromEntries(TASKS.map((task) => [task.id, null])) as Record<
    TaskId,
    null
  >;
}

/** 查找一项已持久化任务绑定。 */
export function findTaskBinding(
  catalog: ConfigurationCatalog | null,
  taskId: TaskId,
): TaskBinding | undefined {
  return catalog?.task_bindings.find((binding) => binding.task_id === taskId);
}
