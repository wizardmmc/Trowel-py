/** 管理连接编辑器和 Agent 默认草稿的纯状态转换。 */

import type {
  AgentDefaults,
  CodexCatalogEntry,
  Connection,
  ConnectionDraft,
  ConnectionEditorState,
  ConnectionKind,
} from "../domain/types";

/** 用新候选刷新已选模型元数据，同时保持用户选择和顺序。 */
export function refreshSelectedCodexCatalog(
  selected: readonly CodexCatalogEntry[],
  candidates: readonly CodexCatalogEntry[],
): CodexCatalogEntry[] {
  const byId = new Map(candidates.map((entry) => [entry.id, entry]));
  return selected.map((entry) => {
    const candidate = byId.get(entry.id);
    if (!candidate) return entry;
    const keepsEffort = Boolean(
      entry.default_effort &&
        candidate.supported_efforts.includes(entry.default_effort),
    );
    return {
      ...candidate,
      default_effort: keepsEffort
        ? entry.default_effort
        : candidate.default_effort,
    };
  });
}

/** 修改这些字段后，上一次模型目录身份立即失效。 */
export const MODEL_IDENTITY_FIELDS = new Set<keyof ConnectionDraft>([
  "runtime",
  "kind",
  "protocol",
  "base_url",
  "models_url",
  "login_directory",
]);

/** 把连接读模型转换成不含 secret 原值的编辑草稿。 */
export function editorFromConnection(
  connection: Connection,
): ConnectionEditorState {
  return {
    connectionId: connection.id,
    version: connection.version,
    draft: connectionDraftFromConnection(connection),
    dirty: false,
    saving: false,
    deleting: false,
    inheritingRuntimeConfig: false,
    error: null,
    conflict: false,
    modelFetch: {
      status: normalizeCatalogStatus(connection.catalog.status),
      models:
        connection.runtime === "codex" && connection.codex_catalog.length > 0
          ? connection.codex_catalog.map((entry) => entry.id)
          : connection.catalog.models,
      codexCatalog: connection.codex_catalog,
      sourceEndpoint: connection.catalog.source_endpoint,
      fetchedAt: connection.catalog.fetched_at,
      requestIdentity: connection.catalog.request_identity,
      error: connection.catalog.error_code,
    },
    officialAccount: idleOfficialAccount(),
  };
}

/** 按连接类型建立合法的最小新建草稿。 */
export function newConnectionEditor(
  kind: ConnectionKind,
): ConnectionEditorState {
  const runtime =
    kind === "claude_compatible"
      ? "claude_code"
      : kind === "direct_api"
        ? "direct_api"
        : "codex";
  const protocol =
    kind === "codex_official"
      ? "codex_official"
      : kind === "claude_compatible"
        ? "anthropic_messages"
        : "openai_responses";
  return {
    connectionId: null,
    version: 0,
    draft: {
      name: "",
      runtime,
      kind,
      protocol,
      base_url: null,
      models_url: null,
      login_directory: null,
      proxy_url: null,
      proxy_username: null,
      claude_role_models: {},
      codex_catalog: [],
      catalog_request_identity: null,
    },
    dirty: false,
    saving: false,
    deleting: false,
    inheritingRuntimeConfig: false,
    error: null,
    conflict: false,
    modelFetch: idleModelFetch(),
    officialAccount: idleOfficialAccount(),
  };
}

/** 清空失效候选，保留 stale 状态供 UI 解释。 */
export function staleModelFetch(
  current: ConnectionEditorState["modelFetch"],
): ConnectionEditorState["modelFetch"] {
  if (current.status === "idle") return idleModelFetch();
  return {
    ...current,
    status: "stale",
    models: [],
    codexCatalog: [],
    requestIdentity: null,
    error: null,
  };
}

/** 对比会改变上游模型请求身份的非敏感字段。 */
export function connectionFingerprint(draft: ConnectionDraft): string {
  return JSON.stringify({
    runtime: draft.runtime,
    kind: draft.kind,
    protocol: draft.protocol,
    base_url: draft.base_url,
    models_url: draft.models_url,
    login_directory: draft.login_directory,
  });
}

/** 判断保存返回后连接草稿是否仍是当时提交的内容。 */
export function connectionDraftEquals(
  current: ConnectionDraft,
  submitted: ConnectionDraft,
): boolean {
  return JSON.stringify(current) === JSON.stringify(submitted);
}

/** 比较 Agent 默认的可编辑字段，不让远端新版本号干扰脏状态判断。 */
export function agentDefaultsInputsEqual(
  current: AgentDefaults,
  submitted: AgentDefaults,
): boolean {
  return (
    current.session_configuration_id === submitted.session_configuration_id &&
    current.permission === submitted.permission &&
    current.memory_enabled === submitted.memory_enabled &&
    current.profile_enabled === submitted.profile_enabled &&
    current.self_enabled === submitted.self_enabled
  );
}

/** 提取连接全部可编辑非敏感字段。 */
function connectionDraftFromConnection(connection: Connection): ConnectionDraft {
  return {
    name: connection.name,
    runtime: connection.runtime,
    kind: connection.kind,
    protocol: connection.protocol,
    base_url: connection.base_url,
    models_url: connection.models_url,
    login_directory: connection.login_directory,
    proxy_url: connection.proxy.url,
    proxy_username: connection.proxy.username,
    claude_role_models: connection.claude_role_models,
    codex_catalog: connection.codex_catalog,
    catalog_request_identity: connection.catalog.request_identity,
  };
}

/** 把后端目录状态收敛到前端有限状态。 */
function normalizeCatalogStatus(
  status: string,
): ConnectionEditorState["modelFetch"]["status"] {
  return ["ready", "error", "stale"].includes(status)
    ? (status as ConnectionEditorState["modelFetch"]["status"])
    : "idle";
}

/** 创建尚未获取模型的初态。 */
function idleModelFetch(): ConnectionEditorState["modelFetch"] {
  return {
    status: "idle",
    models: [],
    codexCatalog: [],
    sourceEndpoint: null,
    fetchedAt: null,
    requestIdentity: null,
    error: null,
  };
}

/** 创建尚未读取的 Official 账号状态。 */
function idleOfficialAccount(): ConnectionEditorState["officialAccount"] {
  return {
    status: "idle",
    account: null,
    login: null,
    loginBaselineEmail: null,
    loginStarting: false,
    error: null,
  };
}
