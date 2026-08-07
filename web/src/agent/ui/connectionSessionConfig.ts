/** 定义设置连接驱动的可复用会话配置和稳定默认选择规则。 */

import type {
  AgentConnectionOption,
  PermissionPreset,
  Runtime,
} from "../application";
import { CLAUDE_SESSION_EFFORTS } from "../../lib/runtimeEffort";

export { CLAUDE_SESSION_EFFORTS } from "../../lib/runtimeEffort";

export interface ConnectionSessionConfig {
  readonly runtime: Runtime;
  readonly connection_id: string;
  readonly model: string;
  readonly effort: string;
  readonly permission_mode: string;
  readonly permission_preset?: PermissionPreset;
  readonly memory_enabled: boolean;
  readonly profile_enabled: boolean;
  readonly self_enabled: boolean;
}

/** 根据可用连接生成不跨 runtime 泄漏选择的默认配置。 */
export function defaultConnectionSessionConfig(
  connections: readonly AgentConnectionOption[],
  runtime?: Runtime,
  permissionDefaults: "agent" | "discussion" = "discussion",
): ConnectionSessionConfig {
  const selectedRuntime =
    runtime ?? connections.find((item) => item.available)?.runtime ?? "claude_code";
  const selectedConnection =
    connections.find(
      (item) => item.runtime === selectedRuntime && item.available,
    ) ?? connections.find((item) => item.runtime === selectedRuntime);
  const model = preferredConnectionModel(selectedConnection);
  return {
    runtime: selectedRuntime,
    connection_id: selectedConnection?.id ?? "",
    model: model?.id ?? "",
    effort: preferredConnectionEffort(selectedConnection, model),
    permission_mode:
      selectedRuntime === "claude_code"
        ? permissionDefaults === "agent"
          ? "bypassPermissions"
          : "dontAsk"
        : "",
    permission_preset:
      selectedRuntime === "codex"
        ? permissionDefaults === "agent"
          ? "follow"
          : "read-only"
        : undefined,
    memory_enabled: true,
    profile_enabled: true,
    self_enabled: true,
  };
}

/** 优先恢复连接上次成功模型，否则选择第一个可用模型。 */
export function preferredConnectionModel(connection?: AgentConnectionOption) {
  return (
    connection?.models.find(
      (item) =>
        item.available && item.id === connection.last_session_choice?.model,
    ) ?? connection?.models.find((item) => item.available)
  );
}

/** 仅在当前模型仍支持时恢复上次强度。 */
export function preferredConnectionEffort(
  connection: AgentConnectionOption | undefined,
  model: AgentConnectionOption["models"][number] | undefined,
): string {
  const last = connection?.last_session_choice?.effort ?? "";
  if (connection?.runtime === "claude_code") {
    return CLAUDE_SESSION_EFFORTS.includes(
      last as (typeof CLAUDE_SESSION_EFFORTS)[number],
    )
      ? last
      : "";
  }
  if (model?.efforts.includes(last)) return last;
  return model?.default_effort ?? model?.efforts[0] ?? "";
}
