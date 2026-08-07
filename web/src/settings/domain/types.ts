/** 定义设置页消费的脱敏配置契约和本地编辑状态。 */

export type RuntimeKind = "claude_code" | "codex" | "direct_api";
export type ConnectionKind =
  | "claude_compatible"
  | "codex_official"
  | "codex_custom"
  | "direct_api";
export type ProtocolKind =
  | "anthropic_messages"
  | "openai_responses"
  | "codex_official";
export type SecretKind = "api_key" | "proxy_password";
export type TaskId =
  | "memory_refine"
  | "profile_distill"
  | "memory_daily"
  | "memory_weekly"
  | "memory_monthly";
export type SettingsSection =
  | "paths"
  | "connections"
  | "tasks"
  | "agent"
  | "diagnostics"
  | "about";

export interface AuthStatus {
  readonly kind: string;
  readonly status: string;
}

export interface ProxyStatus {
  readonly url: string | null;
  readonly username: string | null;
  readonly password_status: string;
}

export interface ModelCatalog {
  readonly status: string;
  readonly models: readonly string[];
  readonly source_endpoint: string | null;
  readonly fetched_at: string | null;
  readonly request_identity: string | null;
  readonly error_code: string | null;
}

export interface CodexCatalogEntry {
  readonly id: string;
  readonly display_name: string | null;
  readonly default_effort: string | null;
  readonly supported_efforts: readonly string[];
}

export interface CodexOfficialAccount {
  readonly status: "logged_in" | "not_logged_in" | "unsupported";
  readonly email: string | null;
  readonly plan_type: string | null;
  readonly auth_mode: string | null;
  readonly login_id?: string | null;
  readonly login_status?: "pending" | "completed" | "failed" | null;
  readonly login_error?: string | null;
}

export interface CodexOfficialLogin {
  readonly login_id: string;
  readonly verification_url: string;
  readonly user_code: string;
}

export interface CodexOfficialAccountState {
  readonly status: "idle" | "loading" | "ready" | "error";
  readonly account: CodexOfficialAccount | null;
  readonly login: CodexOfficialLogin | null;
  readonly loginBaselineEmail: string | null;
  readonly loginStarting: boolean;
  readonly error: string | null;
}

export interface Connection {
  readonly id: string;
  readonly version: number;
  readonly identity_version: number;
  readonly name: string;
  readonly runtime: RuntimeKind;
  readonly kind: ConnectionKind;
  readonly protocol: ProtocolKind;
  readonly base_url: string | null;
  readonly models_url: string | null;
  readonly upstream_host: string | null;
  readonly auth: AuthStatus;
  readonly login_directory: string | null;
  readonly login_directory_exists: boolean | null;
  readonly claude_config_inherited: boolean | null;
  readonly codex_config_inherited: boolean | null;
  readonly proxy: ProxyStatus;
  readonly claude_role_models: Readonly<Record<string, string>>;
  readonly codex_catalog: readonly CodexCatalogEntry[];
  readonly catalog: ModelCatalog;
  readonly validation_status: string;
  readonly capability_version: string;
  readonly last_session_choice: Readonly<Record<string, string | null>> | null;
  readonly secret_versions: Readonly<Record<string, number>>;
  readonly preview: Readonly<Record<string, unknown>>;
}

export interface Capability {
  readonly status: string;
  readonly version: string;
  readonly source: string;
  readonly eligible_tasks: readonly TaskId[];
}

export interface SessionConfiguration {
  readonly id: string;
  readonly version: number;
  readonly name: string;
  readonly runtime: RuntimeKind;
  readonly connection_id: string;
  readonly connection_identity_version: number;
  readonly model: string;
  readonly effort: string | null;
  readonly capability: Capability;
  readonly availability: string;
  readonly disabled_reason: string | null;
}

export interface TaskBinding {
  readonly task_id: TaskId;
  readonly version: number;
  readonly session_configuration_id: string | null;
}

export interface AgentDefaults {
  readonly version: number;
  readonly session_configuration_id: string | null;
  readonly permission: string | null;
  readonly memory_enabled: boolean;
  readonly profile_enabled: boolean;
  readonly self_enabled: boolean;
}

export interface ConfigurationCatalog {
  readonly connections: readonly Connection[];
  readonly session_configurations: readonly SessionConfiguration[];
  readonly task_bindings: readonly TaskBinding[];
  readonly agent_defaults: AgentDefaults;
}

export interface PathEntry {
  readonly path: string;
  readonly exists: boolean;
  readonly kind: "file" | "directory" | string;
}

export interface PathStatus {
  readonly data_mode: string;
  readonly paths: Readonly<Record<string, PathEntry>>;
}

export interface DiagnosticLayer {
  readonly status:
    | "available"
    | "unavailable"
    | "unknown"
    | "unsupported"
    | "not_applicable"
    | string;
  readonly code: string | null;
}

export interface ConnectionDiagnostic {
  readonly connection_id: string;
  readonly connection_name: string;
  readonly network: DiagnosticLayer;
  readonly runtime_launch: DiagnosticLayer;
  readonly trowel_proxy: DiagnosticLayer;
}

export interface Diagnostics {
  readonly connections: readonly ConnectionDiagnostic[];
}

export interface ConnectionDraft {
  readonly name: string;
  readonly runtime: RuntimeKind;
  readonly kind: ConnectionKind;
  readonly protocol: ProtocolKind;
  readonly base_url: string | null;
  readonly models_url: string | null;
  readonly login_directory: string | null;
  readonly proxy_url: string | null;
  readonly proxy_username: string | null;
  readonly claude_role_models: Readonly<Record<string, string>>;
  readonly codex_catalog: readonly CodexCatalogEntry[];
  readonly catalog_request_identity: string | null;
}

export interface FetchModelsResult {
  readonly status: "ready";
  readonly models: readonly string[];
  readonly source_endpoint: string | null;
  readonly fetched_at: string | null;
  readonly request_identity: string | null;
  readonly connection_version: number;
  readonly codex_catalog: readonly CodexCatalogEntry[];
}

export interface SecretStatusResult {
  readonly connection_id: string;
  readonly version: number;
  readonly status: string;
}

export interface ModelFetchState {
  readonly status: "idle" | "loading" | "ready" | "error" | "stale";
  readonly models: readonly string[];
  readonly codexCatalog: readonly CodexCatalogEntry[];
  readonly sourceEndpoint: string | null;
  readonly fetchedAt: string | null;
  readonly requestIdentity: string | null;
  readonly error: string | null;
}

export interface ConnectionEditorState {
  readonly connectionId: string | null;
  readonly version: number;
  readonly draft: ConnectionDraft;
  readonly dirty: boolean;
  readonly saving: boolean;
  readonly deleting: boolean;
  readonly inheritingRuntimeConfig: boolean;
  readonly error: string | null;
  readonly conflict: boolean;
  readonly modelFetch: ModelFetchState;
  readonly officialAccount: CodexOfficialAccountState;
}

export interface ConfigurationErrorBody {
  readonly code: string;
  readonly message: string;
}

export const TASKS: readonly {
  readonly id: TaskId;
  readonly label: string;
  readonly description: string;
}[] = [
  { id: "memory_refine", label: "Memory 精炼", description: "把会话片段提炼成可检索记忆" },
  { id: "profile_distill", label: "Profile 提炼", description: "从证据生成待确认的用户画像建议" },
  { id: "memory_daily", label: "Daily 汇总", description: "生成当天经历摘要" },
  { id: "memory_weekly", label: "Weekly 汇总", description: "整理一周知识和经历" },
  { id: "memory_monthly", label: "Monthly 汇总", description: "整理月度长期脉络" },
] as const;

export const CLAUDE_ROLES = [
  ["default", "默认兜底"],
  ["sonnet", "Sonnet"],
  ["opus", "Opus"],
  ["fable", "Fable"],
  ["haiku", "Haiku"],
  ["subagent", "Subagent"],
] as const;
