/** 统一运行配置在紧凑选择器和完整管理页中的展示名称。 */

import type { Connection, SessionConfiguration } from "./types";

/** 有稳定别名时只显示别名，否则组合模型连接名称与配置名称。 */
export function compactConfigurationLabel(
  configuration: SessionConfiguration,
  connections: readonly Connection[],
): string {
  if (configuration.stable_alias) return configuration.stable_alias;
  const connectionName = configuration.connection_name
    ?? connections.find((item) => item.id === configuration.connection_id)?.name
    ?? "连接已删除";
  return `${connectionName} · ${configuration.name}`;
}
