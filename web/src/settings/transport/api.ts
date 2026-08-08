/** 通过统一平台 transport 访问配置领域的脱敏接口。 */

import { transportFetch } from "../../platform/transport";
import type {
  AgentDefaults,
  ConfigurationCatalog,
  ConfigurationErrorBody,
  Connection,
  ConnectionDraft,
  CodexOfficialAccount,
  CodexOfficialLogin,
  Diagnostics,
  FetchModelsResult,
  PathStatus,
  SecretKind,
  SecretStatusResult,
  SessionConfiguration,
  SessionConfigurationDraft,
  TaskBinding,
  TaskId,
} from "../domain/types";

interface SuccessEnvelope<T> {
  readonly success: true;
  readonly data: T;
  readonly error: null;
}

interface ErrorEnvelope {
  readonly success: false;
  readonly data: null;
  readonly error: ConfigurationErrorBody;
}

/** 保留稳定错误码，供版本冲突等交互作明确分支。 */
export class ConfigurationApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "ConfigurationApiError";
    this.code = code;
    this.status = status;
  }
}

/** 读取设置页一次打开所需的配置事实。 */
export function fetchConfigurationCatalog(): Promise<ConfigurationCatalog> {
  return requestConfiguration("/api/configuration/catalog");
}

/** 读取后端 resolver 给出的真实路径。 */
export function fetchPathStatus(): Promise<PathStatus> {
  return requestConfiguration("/api/configuration/paths");
}

/** 读取所有连接互相独立的三层诊断。 */
export function fetchDiagnostics(): Promise<Diagnostics> {
  return requestConfiguration("/api/configuration/diagnostics");
}

/** 创建一条不含凭据原文的连接。 */
export function createConnection(draft: ConnectionDraft): Promise<Connection> {
  return requestConfiguration("/api/configuration/connections", {
    method: "POST",
    body: JSON.stringify(draft),
  });
}

/** 按调用方读取版本替换连接的非敏感字段。 */
export function updateConnection(
  id: string,
  expectedVersion: number,
  draft: ConnectionDraft,
): Promise<Connection> {
  return requestConfiguration(`/api/configuration/connections/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify({ ...draft, expected_version: expectedVersion }),
  });
}

/** 按乐观版本软删除连接及其凭据。 */
export function deleteConnection(id: string, expectedVersion: number): Promise<null> {
  const query = new URLSearchParams({ expected_version: String(expectedVersion) });
  return requestConfiguration(
    `/api/configuration/connections/${encodeURIComponent(id)}?${query.toString()}`,
    { method: "DELETE" },
  );
}

/** 把真实全局 Claude 用户配置的受支持部分覆盖继承到连接家。 */
export function inheritGlobalClaudeConfig(
  id: string,
  expectedVersion: number,
): Promise<Connection> {
  const query = new URLSearchParams({ expected_version: String(expectedVersion) });
  return requestConfiguration(
    `/api/configuration/connections/${encodeURIComponent(id)}/claude-config/inherit?${query.toString()}`,
    { method: "POST" },
  );
}

/** 把真实全局 Codex 配置的受支持部分覆盖继承到连接家。 */
export function inheritGlobalCodexConfig(
  id: string,
  expectedVersion: number,
): Promise<Connection> {
  const query = new URLSearchParams({ expected_version: String(expectedVersion) });
  return requestConfiguration(
    `/api/configuration/connections/${encodeURIComponent(id)}/codex-config/inherit?${query.toString()}`,
    { method: "POST" },
  );
}

/** 写入只在本次调用内存在的凭据，不返回原值。 */
export function writeSecret(
  id: string,
  kind: SecretKind,
  expectedVersion: number,
  value: string,
): Promise<SecretStatusResult> {
  return requestConfiguration(
    `/api/configuration/connections/${encodeURIComponent(id)}/secrets/${kind}`,
    {
      method: "PUT",
      body: JSON.stringify({
        expected_version: expectedVersion,
        action: "set",
        value,
      }),
    },
  );
}

/** 删除一项已保存凭据，不读取其原文。 */
export function deleteSecret(
  id: string,
  kind: SecretKind,
  expectedVersion: number,
): Promise<SecretStatusResult> {
  return requestConfiguration(
    `/api/configuration/connections/${encodeURIComponent(id)}/secrets/${kind}`,
    {
      method: "PUT",
      body: JSON.stringify({ expected_version: expectedVersion, action: "delete" }),
    },
  );
}

/** 使用后端保存的凭据按当前非敏感草稿获取模型列表。 */
export function fetchModels(
  id: string,
  expectedVersion: number,
  draft: ConnectionDraft,
): Promise<FetchModelsResult> {
  return requestConfiguration(
    `/api/configuration/connections/${encodeURIComponent(id)}/models:fetch`,
    {
      method: "POST",
      body: JSON.stringify({ expected_version: expectedVersion, draft }),
    },
  );
}

/** 读取一项 Official 供应商的 Codex 原生账号摘要。 */
export function fetchCodexOfficialAccount(
  id: string,
): Promise<CodexOfficialAccount> {
  return requestConfiguration(
    `/api/configuration/connections/${encodeURIComponent(id)}/official-account`,
  );
}

/** 在供应商自己的账号槽位启动 Codex 原生 device-code 登录。 */
export function startCodexOfficialLogin(
  id: string,
): Promise<CodexOfficialLogin> {
  return requestConfiguration(
    `/api/configuration/connections/${encodeURIComponent(id)}/official-account/login`,
    { method: "POST" },
  );
}

/** 创建一份由多个 Agent 场景复用的运行配置。 */
export function createSessionConfiguration(
  draft: SessionConfigurationDraft,
  expectedConnectionVersion: number,
): Promise<SessionConfiguration> {
  return requestConfiguration("/api/configuration/session-configurations", {
    method: "POST",
    body: JSON.stringify({
      ...draft,
      expected_connection_version: expectedConnectionVersion,
    }),
  });
}

/** 按乐观版本完整替换一份运行配置。 */
export function updateSessionConfiguration(
  id: string,
  expectedVersion: number,
  expectedConnectionVersion: number,
  draft: SessionConfigurationDraft,
): Promise<SessionConfiguration> {
  return requestConfiguration(
    `/api/configuration/session-configurations/${encodeURIComponent(id)}`,
    {
      method: "PUT",
      body: JSON.stringify({
        ...draft,
        expected_version: expectedVersion,
        expected_connection_version: expectedConnectionVersion,
      }),
    },
  );
}

/** 归档运行配置并保留 Agent 默认和后台任务中的原始引用。 */
export function archiveSessionConfiguration(
  id: string,
  expectedVersion: number,
): Promise<null> {
  const query = new URLSearchParams({ expected_version: String(expectedVersion) });
  return requestConfiguration(
    `/api/configuration/session-configurations/${encodeURIComponent(id)}?${query.toString()}`,
    { method: "DELETE" },
  );
}

/** 创建或替换一项后台任务绑定。 */
export function putTaskBinding(
  taskId: TaskId,
  configurationId: string,
  expectedVersion: number,
): Promise<TaskBinding> {
  return requestConfiguration(
    `/api/configuration/task-bindings/${encodeURIComponent(taskId)}`,
    {
      method: "PUT",
      body: JSON.stringify({
        session_configuration_id: configurationId,
        expected_version: expectedVersion,
      }),
    },
  );
}

/** 解除一项已有后台任务绑定。 */
export function deleteTaskBinding(
  taskId: TaskId,
  expectedVersion: number,
): Promise<TaskBinding> {
  const query = new URLSearchParams({ expected_version: String(expectedVersion) });
  return requestConfiguration(
    `/api/configuration/task-bindings/${encodeURIComponent(taskId)}?${query.toString()}`,
    { method: "DELETE" },
  );
}

/** 保存只影响之后新建会话的 Agent 默认条件。 */
export function putAgentDefaults(defaults: AgentDefaults): Promise<AgentDefaults> {
  return requestConfiguration("/api/configuration/agent-defaults", {
    method: "PUT",
    body: JSON.stringify({
      expected_version: defaults.version,
      session_configuration_id: defaults.session_configuration_id,
      permission: defaults.permission,
      memory_enabled: defaults.memory_enabled,
      profile_enabled: defaults.profile_enabled,
      self_enabled: defaults.self_enabled,
    }),
  });
}

/** 解析配置领域的统一 envelope，错误消息只采用后端脱敏字段。 */
async function requestConfiguration<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const headers = new Headers(options?.headers);
  if (options?.body !== undefined) headers.set("Content-Type", "application/json");
  const response = await transportFetch(path, { ...options, headers });
  let envelope: SuccessEnvelope<T> | ErrorEnvelope;
  try {
    envelope = (await response.json()) as SuccessEnvelope<T> | ErrorEnvelope;
  } catch {
    throw new ConfigurationApiError(
      "INVALID_CONFIGURATION_RESPONSE",
      "配置服务返回了无法识别的响应",
      response.status,
    );
  }
  if (!response.ok || !envelope.success) {
    const error = envelope.success
      ? { code: "CONFIGURATION_REQUEST_FAILED", message: "配置请求失败" }
      : envelope.error;
    throw new ConfigurationApiError(error.code, error.message, response.status);
  }
  return envelope.data;
}
