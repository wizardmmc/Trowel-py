/** 提供只依赖配置 catalog 的设置页资格选择器。 */

import type {
  ConfigurationCatalog,
  SessionConfiguration,
  TaskId,
} from "../domain/types";

/** 返回后端明确允许该任务且当前仍可用的会话配置。 */
export function eligibleSessionConfigurations(
  catalog: ConfigurationCatalog,
  taskId: TaskId,
): readonly SessionConfiguration[] {
  return catalog.session_configurations.filter(
    (configuration) =>
      configuration.availability === "available" &&
      configuration.capability.eligible_tasks.includes(taskId),
  );
}

/** 返回可创建 Agent 会话的配置，排除只服务后台任务的 direct API。 */
export function eligibleAgentSessionConfigurations(
  catalog: ConfigurationCatalog,
): readonly SessionConfiguration[] {
  return catalog.session_configurations.filter(
    (configuration) =>
      configuration.availability === "available" &&
      configuration.runtime !== "direct_api",
  );
}
