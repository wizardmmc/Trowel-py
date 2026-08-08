/** 统一拥有设置页远端事实、草稿、乐观并发和模型请求时序。 */

import { createStore } from "zustand/vanilla";
import type {
  AgentDefaults,
  ConfigurationCatalog,
  Connection,
  ConnectionDraft,
  CodexOfficialAccount,
  CodexOfficialLogin,
  ConnectionEditorState,
  ConnectionKind,
  Diagnostics,
  FetchModelsResult,
  PathStatus,
  SecretKind,
  SecretStatusResult,
  SessionConfiguration,
  SessionConfigurationDraft,
  SessionConfigurationEditorState,
  SettingsSection,
  TaskBinding,
  TaskId,
} from "../domain/types";
import {
  ConfigurationApiError,
  archiveSessionConfiguration,
  createConnection,
  createSessionConfiguration,
  deleteConnection,
  inheritGlobalClaudeConfig,
  inheritGlobalCodexConfig,
  deleteSecret,
  deleteTaskBinding,
  fetchConfigurationCatalog,
  fetchDiagnostics,
  fetchModels,
  fetchCodexOfficialAccount,
  fetchPathStatus,
  putAgentDefaults,
  putTaskBinding,
  startCodexOfficialLogin,
  updateConnection,
  updateSessionConfiguration,
  writeSecret,
} from "../transport/api";
import {
  agentDefaultsInputsEqual,
  connectionDraftEquals,
  connectionFingerprint,
  editorFromConnection,
  MODEL_IDENTITY_FIELDS,
  newConnectionEditor,
  refreshSelectedCodexCatalog,
  staleModelFetch,
} from "./editorState";
import {
  emptyTaskErrors,
  emptyTaskFlags,
  emptyTaskValues,
  findTaskBinding,
  type TaskErrors,
  type TaskFlags,
  type TaskValues,
} from "./taskState";

export interface SettingsApi {
  readonly fetchCatalog: () => Promise<ConfigurationCatalog>;
  readonly fetchPaths: () => Promise<PathStatus>;
  readonly fetchDiagnostics: () => Promise<Diagnostics>;
  readonly createConnection: (draft: ConnectionDraft) => Promise<Connection>;
  readonly updateConnection: (
    id: string,
    expectedVersion: number,
    draft: ConnectionDraft,
  ) => Promise<Connection>;
  readonly deleteConnection: (id: string, expectedVersion: number) => Promise<null>;
  readonly inheritGlobalClaudeConfig: (
    id: string,
    expectedVersion: number,
  ) => Promise<Connection>;
  readonly inheritGlobalCodexConfig: (
    id: string,
    expectedVersion: number,
  ) => Promise<Connection>;
  readonly writeSecret: (
    id: string,
    kind: SecretKind,
    expectedVersion: number,
    value: string,
  ) => Promise<SecretStatusResult>;
  readonly deleteSecret: (
    id: string,
    kind: SecretKind,
    expectedVersion: number,
  ) => Promise<SecretStatusResult>;
  readonly fetchModels: (
    id: string,
    expectedVersion: number,
    draft: ConnectionDraft,
  ) => Promise<FetchModelsResult>;
  readonly fetchCodexOfficialAccount: (id: string) => Promise<CodexOfficialAccount>;
  readonly startCodexOfficialLogin: (id: string) => Promise<CodexOfficialLogin>;
  readonly createSessionConfiguration: (
    draft: SessionConfigurationDraft,
    expectedConnectionVersion: number,
  ) => Promise<SessionConfiguration>;
  readonly updateSessionConfiguration: (
    id: string,
    expectedVersion: number,
    expectedConnectionVersion: number,
    draft: SessionConfigurationDraft,
  ) => Promise<SessionConfiguration>;
  readonly archiveSessionConfiguration: (
    id: string,
    expectedVersion: number,
  ) => Promise<null>;
  readonly putTaskBinding: (
    taskId: TaskId,
    configurationId: string,
    expectedVersion: number,
  ) => Promise<TaskBinding>;
  readonly deleteTaskBinding: (
    taskId: TaskId,
    expectedVersion: number,
  ) => Promise<TaskBinding>;
  readonly putAgentDefaults: (defaults: AgentDefaults) => Promise<AgentDefaults>;
}

export interface SettingsState {
  readonly activeSection: SettingsSection;
  readonly loading: boolean;
  readonly initialized: boolean;
  readonly error: string | null;
  readonly catalog: ConfigurationCatalog | null;
  readonly paths: PathStatus | null;
  readonly pathsError: string | null;
  readonly diagnostics: Diagnostics | null;
  readonly diagnosticsError: string | null;
  readonly diagnosticsFetchedAt: string | null;
  readonly connectionEditor: ConnectionEditorState | null;
  readonly configurationEditor: SessionConfigurationEditorState | null;
  readonly connectionRuntimeFilter: "claude_code" | "codex";
  readonly taskDrafts: TaskValues;
  readonly taskEnabled: TaskFlags;
  readonly taskSaving: TaskFlags;
  readonly taskDirty: TaskFlags;
  readonly taskErrors: TaskErrors;
  readonly agentDraft: AgentDefaults;
  readonly agentDirty: boolean;
  readonly agentSaving: boolean;
  readonly agentError: string | null;
  readonly agentConflict: boolean;
  readonly setActiveSection: (section: SettingsSection) => void;
  readonly initialize: () => Promise<void>;
  readonly reloadCatalog: () => Promise<boolean>;
  readonly refreshPaths: () => Promise<void>;
  readonly refreshDiagnostics: () => Promise<void>;
  readonly openConnection: (connectionId: string) => void;
  readonly createConnectionDraft: (kind: ConnectionKind) => void;
  readonly changeNewConnectionKind: (kind: ConnectionKind) => void;
  readonly setConnectionRuntimeFilter: (runtime: "claude_code" | "codex") => void;
  readonly closeConnection: () => void;
  readonly updateConnectionDraft: (patch: Partial<ConnectionDraft>) => void;
  readonly updateClaudeRole: (role: string, model: string) => void;
  readonly saveConnection: () => Promise<void>;
  readonly removeConnection: () => Promise<void>;
  readonly inheritRuntimeConfig: () => Promise<boolean>;
  readonly fetchConnectionModels: () => Promise<void>;
  readonly refreshCodexOfficialAccount: () => Promise<void>;
  readonly beginCodexOfficialLogin: () => Promise<CodexOfficialLogin | null>;
  readonly writeConnectionSecret: (kind: SecretKind, value: string) => Promise<void>;
  readonly deleteConnectionSecret: (kind: SecretKind) => Promise<void>;
  readonly openSessionConfiguration: (configurationId: string) => void;
  readonly createSessionConfigurationDraft: () => void;
  readonly closeSessionConfiguration: () => void;
  readonly updateSessionConfigurationDraft: (
    patch: Partial<SessionConfigurationDraft>,
  ) => void;
  readonly saveSessionConfiguration: () => Promise<void>;
  readonly archiveSessionConfiguration: () => Promise<void>;
  readonly setTaskDraft: (taskId: TaskId, configurationId: string | null) => void;
  readonly setTaskEnabled: (taskId: TaskId, enabled: boolean) => void;
  readonly saveTask: (taskId: TaskId) => Promise<void>;
  readonly reloadTask: (taskId: TaskId) => void;
  readonly updateAgentDraft: (patch: Partial<AgentDefaults>) => void;
  readonly saveAgentDefaults: () => Promise<void>;
  readonly reloadAgentDefaults: () => void;
}

const defaultApi: SettingsApi = {
  fetchCatalog: fetchConfigurationCatalog,
  fetchPaths: fetchPathStatus,
  fetchDiagnostics,
  createConnection,
  updateConnection,
  deleteConnection,
  inheritGlobalClaudeConfig,
  inheritGlobalCodexConfig,
  writeSecret,
  deleteSecret,
  fetchModels,
  fetchCodexOfficialAccount,
  putTaskBinding,
  startCodexOfficialLogin,
  createSessionConfiguration,
  updateSessionConfiguration,
  archiveSessionConfiguration,
  deleteTaskBinding,
  putAgentDefaults,
};

const EMPTY_AGENT_DEFAULTS: AgentDefaults = {
  version: 0,
  session_configuration_id: null,
  permission: null,
  memory_enabled: true,
  profile_enabled: true,
  self_enabled: true,
};

/** 创建隔离设置状态，测试可替换任意 API 操作。 */
export function createSettingsStore(apiOverrides: Partial<SettingsApi> = {}) {
  const api = { ...defaultApi, ...apiOverrides };
  let latestModelRequest = 0;
  let latestOfficialAccountRequest = 0;

  return createStore<SettingsState>((set, get) => ({
    activeSection: "paths",
    loading: false,
    initialized: false,
    error: null,
    catalog: null,
    paths: null,
    pathsError: null,
    diagnostics: null,
    diagnosticsError: null,
    diagnosticsFetchedAt: null,
    connectionEditor: null,
    configurationEditor: null,
    connectionRuntimeFilter: "claude_code",
    taskDrafts: emptyTaskValues(),
    taskEnabled: emptyTaskFlags(false),
    taskSaving: emptyTaskFlags(false),
    taskDirty: emptyTaskFlags(false),
    taskErrors: emptyTaskErrors(),
    agentDraft: EMPTY_AGENT_DEFAULTS,
    agentDirty: false,
    agentSaving: false,
    agentError: null,
    agentConflict: false,
    setActiveSection: (activeSection) => set({ activeSection }),
    initialize: async () => {
      if (get().loading || (get().initialized && get().catalog !== null)) return;
      set({ loading: true, error: null });
      try {
        const [catalogResult, pathsResult, diagnosticsResult] = await Promise.allSettled([
          api.fetchCatalog(),
          api.fetchPaths(),
          api.fetchDiagnostics(),
        ]);
        if (catalogResult.status === "fulfilled") {
          setCatalogFacts(set, catalogResult.value);
        }
        set({
          paths: pathsResult.status === "fulfilled" ? pathsResult.value : null,
          pathsError:
            pathsResult.status === "rejected"
              ? errorMessage(pathsResult.reason)
              : null,
          diagnostics:
            diagnosticsResult.status === "fulfilled"
              ? diagnosticsResult.value
              : null,
          diagnosticsError:
            diagnosticsResult.status === "rejected"
              ? errorMessage(diagnosticsResult.reason)
              : null,
          diagnosticsFetchedAt:
            diagnosticsResult.status === "fulfilled"
              ? new Date().toISOString()
              : null,
          loading: false,
          initialized: true,
          error:
            catalogResult.status === "rejected"
              ? errorMessage(catalogResult.reason)
              : null,
        });
      } catch (error) {
        // Promise.allSettled 本身通常不会失败，仍为意外调度错误保留稳定降级。
        set({ loading: false, initialized: true, error: errorMessage(error) });
      }
    },
    reloadCatalog: async () => {
      try {
        const catalog = await api.fetchCatalog();
        setCatalogFacts(set, catalog);
        set({ error: null });
        return true;
      } catch (error) {
        set({ error: errorMessage(error) });
        return false;
      }
    },
    refreshPaths: async () => {
      try {
        const paths = await api.fetchPaths();
        set({ paths, pathsError: null });
      } catch (error) {
        set({ pathsError: errorMessage(error) });
      }
    },
    refreshDiagnostics: async () => {
      try {
        const diagnostics = await api.fetchDiagnostics();
        set({
          diagnostics,
          diagnosticsFetchedAt: new Date().toISOString(),
          diagnosticsError: null,
        });
      } catch (error) {
        set({ diagnosticsError: errorMessage(error) });
      }
    },
    openConnection: (connectionId) => {
      latestModelRequest += 1;
      latestOfficialAccountRequest += 1;
      const connection = get().catalog?.connections.find(
        (item) => item.id === connectionId,
      );
      if (!connection) return;
      set({ connectionEditor: editorFromConnection(connection) });
    },
    createConnectionDraft: (kind) => {
      latestModelRequest += 1;
      latestOfficialAccountRequest += 1;
      set({ connectionEditor: newConnectionEditor(kind) });
    },
    changeNewConnectionKind: (kind) => {
      const editor = get().connectionEditor;
      if (!editor || editor.connectionId) return;
      latestModelRequest += 1;
      latestOfficialAccountRequest += 1;
      const replacement = newConnectionEditor(kind);
      set({
        connectionEditor: {
          ...replacement,
          draft: { ...replacement.draft, name: editor.draft.name },
          dirty: Boolean(editor.draft.name),
        },
      });
    },
    setConnectionRuntimeFilter: (connectionRuntimeFilter) => set({ connectionRuntimeFilter }),
    closeConnection: () => {
      latestModelRequest += 1;
      latestOfficialAccountRequest += 1;
      set({ connectionEditor: null });
    },
    updateConnectionDraft: (patch) => {
      const editor = get().connectionEditor;
      if (!editor) return;
      const invalidatesModels = Object.keys(patch).some((field) =>
        MODEL_IDENTITY_FIELDS.has(field as keyof ConnectionDraft),
      );
      if (invalidatesModels) latestModelRequest += 1;
      set({
        connectionEditor: {
          ...editor,
          draft: invalidatesModels
            ? {
                ...editor.draft,
                ...patch,
                claude_role_models: {},
                codex_catalog: [],
                catalog_request_identity: null,
              }
            : { ...editor.draft, ...patch },
          dirty: true,
          error: null,
          conflict: false,
          modelFetch: invalidatesModels
            ? staleModelFetch(editor.modelFetch)
            : editor.modelFetch,
        },
      });
    },
    updateClaudeRole: (role, model) => {
      const editor = get().connectionEditor;
      if (!editor) return;
      const nextRoles = { ...editor.draft.claude_role_models };
      if (model) nextRoles[role] = model;
      else delete nextRoles[role];
      set({
        connectionEditor: {
          ...editor,
          draft: {
            ...editor.draft,
            claude_role_models: nextRoles,
            catalog_request_identity: editor.modelFetch.requestIdentity,
          },
          dirty: true,
          error: null,
          conflict: false,
        },
      });
    },
    saveConnection: async () => {
      const editor = get().connectionEditor;
      if (!editor || editor.saving) return;
      set({ connectionEditor: { ...editor, saving: true, error: null, conflict: false } });
      try {
        const connection = editor.connectionId
          ? await api.updateConnection(editor.connectionId, editor.version, editor.draft)
          : await api.createConnection(editor.draft);
        replaceConnection(set, get, connection);
        const current = get().connectionEditor;
        if (!current?.saving || current.connectionId !== editor.connectionId) return;
        latestModelRequest += 1;
        set({
          connectionEditor: connectionDraftEquals(current.draft, editor.draft)
            ? editorFromConnection(connection)
            : {
                ...current,
                connectionId: connection.id,
                version: connection.version,
                dirty: true,
                saving: false,
                deleting: false,
                error: null,
                conflict: false,
              },
        });
      } catch (error) {
        updateEditorError(set, get, error, editor.connectionId, "saving");
      }
    },
    removeConnection: async () => {
      const editor = get().connectionEditor;
      if (!editor?.connectionId || editor.deleting) return;
      set({ connectionEditor: { ...editor, deleting: true, error: null, conflict: false } });
      try {
        await api.deleteConnection(editor.connectionId, editor.version);
        const currentCatalog = get().catalog;
        const currentEditor = get().connectionEditor;
        const deletingCurrent =
          currentEditor?.connectionId === editor.connectionId && currentEditor.deleting;
        if (deletingCurrent) latestModelRequest += 1;
        set({
          catalog: currentCatalog
            ? {
                ...currentCatalog,
                connections: currentCatalog.connections.filter(
                  (item) => item.id !== editor.connectionId,
                ),
              }
            : null,
          connectionEditor: deletingCurrent ? null : currentEditor,
        });
      } catch (error) {
        updateEditorError(set, get, error, editor.connectionId, "deleting");
      }
    },
    inheritRuntimeConfig: async () => {
      const editor = get().connectionEditor;
      if (
        !editor?.connectionId ||
        editor.draft.runtime === "direct_api" ||
        editor.dirty ||
        editor.inheritingRuntimeConfig
      ) return false;
      set({
        connectionEditor: {
          ...editor,
          inheritingRuntimeConfig: true,
          error: null,
          conflict: false,
        },
      });
      try {
        const inherit = editor.draft.runtime === "codex"
          ? api.inheritGlobalCodexConfig
          : api.inheritGlobalClaudeConfig;
        const connection = await inherit(editor.connectionId, editor.version);
        replaceConnection(set, get, connection);
        const current = get().connectionEditor;
        if (
          current?.connectionId !== editor.connectionId ||
          !current.inheritingRuntimeConfig
        ) return true;
        set({ connectionEditor: editorFromConnection(connection) });
        return true;
      } catch (error) {
        const current = get().connectionEditor;
        if (
          current?.connectionId !== editor.connectionId ||
          !current.inheritingRuntimeConfig
        ) return false;
        set({
          connectionEditor: {
            ...current,
            inheritingRuntimeConfig: false,
            error: errorMessage(error),
            conflict: isConflict(error),
          },
        });
        return false;
      }
    },
    fetchConnectionModels: async () => {
      const editor = get().connectionEditor;
      if (!editor?.connectionId || editor.modelFetch.status === "loading") return;
      const request = ++latestModelRequest;
      const fingerprint = connectionFingerprint(editor.draft);
      set({
        connectionEditor: {
          ...editor,
          error: null,
          modelFetch: { ...editor.modelFetch, status: "loading", error: null },
        },
      });
      try {
        const result = await api.fetchModels(
          editor.connectionId,
          editor.version,
          editor.draft,
        );
        const current = get().connectionEditor;
        if (
          request !== latestModelRequest ||
          !current ||
          current.connectionId !== editor.connectionId ||
          connectionFingerprint(current.draft) !== fingerprint
        ) {
          return;
        }
        const catalog = get().catalog;
        const codexCatalog = result.codex_catalog;
        const updatesCodexDraft = current.draft.runtime === "codex";
        const selectedCodexCatalog = updatesCodexDraft
          ? refreshSelectedCodexCatalog(
              current.draft.codex_catalog,
              codexCatalog,
            )
          : current.draft.codex_catalog;
        const selectionMetadataChanged =
          updatesCodexDraft &&
          JSON.stringify(selectedCodexCatalog) !==
            JSON.stringify(current.draft.codex_catalog);
        const fetchedModels = updatesCodexDraft
          ? codexCatalog.map((entry) => entry.id)
          : result.models;
        set({
          catalog: catalog
            ? {
                ...catalog,
                connections: catalog.connections.map((connection) =>
                  connection.id === editor.connectionId
                    ? {
                        ...connection,
                        version: result.connection_version,
                        catalog: {
                          status: "ready",
                          models: result.models,
                          source_endpoint: result.source_endpoint,
                          fetched_at: result.fetched_at,
                          request_identity: result.request_identity,
                          error_code: null,
                        },
                      }
                    : connection,
                ),
              }
            : null,
          connectionEditor: {
            ...current,
            version: result.connection_version,
            draft: updatesCodexDraft
              ? {
                  ...current.draft,
                  codex_catalog: selectedCodexCatalog,
                  catalog_request_identity: result.request_identity,
                }
              : current.draft,
            dirty: current.dirty || selectionMetadataChanged,
            modelFetch: {
              status: "ready",
              models: fetchedModels,
              codexCatalog,
              sourceEndpoint: result.source_endpoint,
              fetchedAt: result.fetched_at,
              requestIdentity: result.request_identity,
              error: null,
            },
          },
        });
      } catch (error) {
        const current = get().connectionEditor;
        if (request !== latestModelRequest || !current?.connectionId) return;
        set({
          connectionEditor: {
            ...current,
            modelFetch: {
              ...current.modelFetch,
              status: "error",
              models: [],
              codexCatalog: [],
              error: errorMessage(error),
            },
          },
        });
        await syncConnectionVersionAfterFetchFailure(api, set, get, current.connectionId);
      }
    },
    refreshCodexOfficialAccount: async () => {
      const editor = get().connectionEditor;
      if (
        !editor?.connectionId ||
        editor.draft.kind !== "codex_official" ||
        editor.officialAccount.status === "loading"
      ) return;
      const request = ++latestOfficialAccountRequest;
      set({
        connectionEditor: {
          ...editor,
          officialAccount: {
            ...editor.officialAccount,
            status: "loading",
            error: null,
          },
        },
      });
      try {
        const account = await api.fetchCodexOfficialAccount(editor.connectionId);
        const current = get().connectionEditor;
        if (
          request !== latestOfficialAccountRequest ||
          current?.connectionId !== editor.connectionId
        ) return;
        const loginCompleted = officialLoginCompleted(
          current.officialAccount,
          account,
        );
        const loginFailed = Boolean(
          current.officialAccount.login &&
            account.login_id === current.officialAccount.login.login_id &&
            account.login_status === "failed",
        );
        const catalog = get().catalog;
        set({
          catalog: catalog
            ? {
                ...catalog,
                connections: catalog.connections.map((connection) =>
                  connection.id === editor.connectionId
                    ? {
                        ...connection,
                        auth: {
                          ...connection.auth,
                          status: account.status === "logged_in" ? "referenced" : "missing",
                        },
                      }
                    : connection,
                ),
              }
            : null,
          connectionEditor: {
            ...current,
            officialAccount: {
              status: "ready",
              account,
              login: loginCompleted || loginFailed ? null : current.officialAccount.login,
              loginBaselineEmail: loginCompleted || loginFailed
                ? null
                : current.officialAccount.loginBaselineEmail,
              loginStarting: false,
              error: loginFailed ? (account.login_error ?? "Codex 登录失败") : null,
            },
          },
        });
      } catch (error) {
        const current = get().connectionEditor;
        if (
          request !== latestOfficialAccountRequest ||
          current?.connectionId !== editor.connectionId
        ) return;
        set({
          connectionEditor: {
            ...current,
            officialAccount: {
              ...current.officialAccount,
              status: "error",
              loginStarting: false,
              error: errorMessage(error),
            },
          },
        });
      }
    },
    beginCodexOfficialLogin: async () => {
      const editor = get().connectionEditor;
      if (!editor?.connectionId || editor.draft.kind !== "codex_official") {
        return null;
      }
      if (editor.officialAccount.loginStarting) return null;
      const request = ++latestOfficialAccountRequest;
      set({
        connectionEditor: {
          ...editor,
          officialAccount: {
            ...editor.officialAccount,
            loginStarting: true,
            error: null,
          },
        },
      });
      try {
        const login = await api.startCodexOfficialLogin(editor.connectionId);
        const current = get().connectionEditor;
        if (
          request !== latestOfficialAccountRequest ||
          current?.connectionId !== editor.connectionId
        ) return null;
        set({
          connectionEditor: {
            ...current,
            officialAccount: {
              ...current.officialAccount,
              status: "ready",
              login,
              loginBaselineEmail: current.officialAccount.account?.email ?? null,
              loginStarting: false,
              error: null,
            },
          },
        });
        return login;
      } catch (error) {
        const current = get().connectionEditor;
        if (
          request === latestOfficialAccountRequest &&
          current?.connectionId === editor.connectionId
        ) {
          set({
            connectionEditor: {
              ...current,
              officialAccount: {
                ...current.officialAccount,
                status: "error",
                loginStarting: false,
                error: errorMessage(error),
              },
            },
          });
        }
        return null;
      }
    },
    writeConnectionSecret: async (kind, value) => {
      const editor = get().connectionEditor;
      if (!editor?.connectionId || !value.trim()) return;
      try {
        const result = await api.writeSecret(
          editor.connectionId,
          kind,
          editor.version,
          value,
        );
        if (get().connectionEditor?.connectionId === editor.connectionId) {
          latestModelRequest += 1;
        }
        applySecretStatus(set, get, kind, result);
      } catch (error) {
        updateEditorErrorForConnection(set, get, error, editor.connectionId);
      }
    },
    deleteConnectionSecret: async (kind) => {
      const editor = get().connectionEditor;
      if (!editor?.connectionId) return;
      try {
        const result = await api.deleteSecret(
          editor.connectionId,
          kind,
          editor.version,
        );
        if (get().connectionEditor?.connectionId === editor.connectionId) {
          latestModelRequest += 1;
        }
        applySecretStatus(set, get, kind, result);
      } catch (error) {
        updateEditorErrorForConnection(set, get, error, editor.connectionId);
      }
    },
    openSessionConfiguration: (configurationId) => {
      const configuration = get().catalog?.session_configurations.find(
        (item) => item.id === configurationId,
      );
      if (!configuration) return;
      set({ configurationEditor: sessionConfigurationEditor(configuration) });
    },
    createSessionConfigurationDraft: () => {
      set({ configurationEditor: emptySessionConfigurationEditor() });
    },
    closeSessionConfiguration: () => set({ configurationEditor: null }),
    updateSessionConfigurationDraft: (patch) => {
      const editor = get().configurationEditor;
      if (!editor) return;
      set({
        configurationEditor: {
          ...editor,
          draft: { ...editor.draft, ...patch },
          dirty: true,
          error: null,
          conflict: false,
        },
      });
    },
    saveSessionConfiguration: async () => {
      const editor = get().configurationEditor;
      const catalog = get().catalog;
      if (!editor || !catalog || editor.saving) return;
      const connection = catalog.connections.find(
        (item) => item.id === editor.draft.connection_id,
      );
      if (!connection) {
        set({ configurationEditor: { ...editor, error: "必须选择模型连接" } });
        return;
      }
      set({ configurationEditor: { ...editor, saving: true, error: null } });
      try {
        const saved = editor.configurationId
          ? await api.updateSessionConfiguration(
              editor.configurationId,
              editor.version,
              connection.version,
              editor.draft,
            )
          : await api.createSessionConfiguration(editor.draft, connection.version);
        replaceSessionConfiguration(set, get, saved);
        set({ configurationEditor: sessionConfigurationEditor(saved) });
      } catch (error) {
        const current = get().configurationEditor;
        if (current?.configurationId !== editor.configurationId) return;
        set({
          configurationEditor: {
            ...current,
            saving: false,
            error: errorMessage(error),
            conflict: isConflict(error),
          },
        });
      }
    },
    archiveSessionConfiguration: async () => {
      const editor = get().configurationEditor;
      if (!editor?.configurationId || editor.archiving) return;
      set({ configurationEditor: { ...editor, archiving: true, error: null } });
      try {
        await api.archiveSessionConfiguration(editor.configurationId, editor.version);
        const catalog = get().catalog;
        set({
          catalog: catalog
            ? {
                ...catalog,
                session_configurations: catalog.session_configurations.filter(
                  (item) => item.id !== editor.configurationId,
                ),
              }
            : null,
          configurationEditor: null,
        });
      } catch (error) {
        const current = get().configurationEditor;
        if (current?.configurationId !== editor.configurationId) return;
        set({
          configurationEditor: {
            ...current,
            archiving: false,
            error: errorMessage(error),
            conflict: isConflict(error),
          },
        });
      }
    },
    setTaskDraft: (taskId, configurationId) => {
      const binding = findTaskBinding(get().catalog, taskId);
      const enabled = get().taskEnabled[taskId];
      const effectiveConfigurationId = enabled ? configurationId : null;
      set({
        taskDrafts: { ...get().taskDrafts, [taskId]: configurationId },
        taskDirty: {
          ...get().taskDirty,
          [taskId]: effectiveConfigurationId !== (binding?.session_configuration_id ?? null),
        },
        taskErrors: { ...get().taskErrors, [taskId]: null },
      });
    },
    setTaskEnabled: (taskId, enabled) => {
      const binding = findTaskBinding(get().catalog, taskId);
      const configurationId = get().taskDrafts[taskId];
      const effectiveConfigurationId = enabled ? configurationId : null;
      set({
        taskEnabled: { ...get().taskEnabled, [taskId]: enabled },
        taskDirty: {
          ...get().taskDirty,
          [taskId]: effectiveConfigurationId !== (binding?.session_configuration_id ?? null),
        },
        taskErrors: { ...get().taskErrors, [taskId]: null },
      });
    },
    saveTask: async (taskId) => {
      if (get().taskSaving[taskId]) return;
      set({
        taskSaving: { ...get().taskSaving, [taskId]: true },
        taskErrors: { ...get().taskErrors, [taskId]: null },
      });
      while (true) {
        const binding = findTaskBinding(get().catalog, taskId);
        const configurationId = effectiveTaskConfiguration(get(), taskId);
        if (configurationId === (binding?.session_configuration_id ?? null)) {
          set({
            taskSaving: { ...get().taskSaving, [taskId]: false },
            taskDirty: { ...get().taskDirty, [taskId]: false },
          });
          return;
        }
        try {
          const updated = configurationId
            ? await api.putTaskBinding(taskId, configurationId, binding?.version ?? 0)
            : binding
              ? await api.deleteTaskBinding(taskId, binding.version)
              : null;
          if (updated) replaceTaskBinding(set, get, updated);
          const persistedConfigurationId = updated?.session_configuration_id ?? null;
          const hasNewerChanges =
            effectiveTaskConfiguration(get(), taskId) !== persistedConfigurationId;
          set({
            taskDirty: { ...get().taskDirty, [taskId]: hasNewerChanges },
          });
          if (!hasNewerChanges) {
            set({ taskSaving: { ...get().taskSaving, [taskId]: false } });
            return;
          }
        } catch (error) {
          set({
            taskSaving: { ...get().taskSaving, [taskId]: false },
            taskDirty: { ...get().taskDirty, [taskId]: true },
            taskErrors: { ...get().taskErrors, [taskId]: errorMessage(error) },
          });
          return;
        }
      }
    },
    reloadTask: (taskId) => {
      const binding = findTaskBinding(get().catalog, taskId);
      set({
        taskDrafts: {
          ...get().taskDrafts,
          [taskId]: binding?.session_configuration_id ?? null,
        },
        taskEnabled: {
          ...get().taskEnabled,
          [taskId]: binding?.session_configuration_id !== null && binding !== undefined,
        },
        taskDirty: { ...get().taskDirty, [taskId]: false },
        taskErrors: { ...get().taskErrors, [taskId]: null },
      });
    },
    updateAgentDraft: (patch) =>
      set({
        agentDraft: { ...get().agentDraft, ...patch },
        agentDirty: true,
        agentError: null,
        agentConflict: false,
      }),
    saveAgentDefaults: async () => {
      if (get().agentSaving || !get().agentDirty) return;
      set({ agentSaving: true, agentError: null, agentConflict: false });
      while (get().agentDirty) {
        const submittedDraft = get().agentDraft;
        try {
          const savedDefaults = await api.putAgentDefaults(submittedDraft);
          const currentCatalog = get().catalog;
          const currentDraft = get().agentDraft;
          const hasNewerChanges = !agentDefaultsInputsEqual(
            currentDraft,
            submittedDraft,
          );
          set({
            catalog: currentCatalog
              ? { ...currentCatalog, agent_defaults: savedDefaults }
              : null,
            agentDraft: hasNewerChanges
              ? { ...currentDraft, version: savedDefaults.version }
              : savedDefaults,
            agentDirty: hasNewerChanges,
          });
        } catch (error) {
          set({
            agentSaving: false,
            agentError: errorMessage(error),
            agentConflict: isConflict(error),
          });
          return;
        }
      }
      set({ agentSaving: false });
    },
    reloadAgentDefaults: () => {
      const defaults = get().catalog?.agent_defaults ?? EMPTY_AGENT_DEFAULTS;
      set({
        agentDraft: defaults,
        agentDirty: false,
        agentError: null,
        agentConflict: false,
      });
    },
  }));
}

export const settingsStore = createSettingsStore();

/** 把一次 catalog 读取同步到绑定草稿和 Agent 默认草稿。 */
function setCatalogFacts(
  set: (patch: Partial<SettingsState>) => void,
  catalog: ConfigurationCatalog,
): void {
  const taskDrafts = emptyTaskValues();
  const taskEnabled = emptyTaskFlags(false) as Record<TaskId, boolean>;
  for (const binding of catalog.task_bindings) {
    (taskDrafts as Record<TaskId, string | null>)[binding.task_id] =
      binding.session_configuration_id;
    taskEnabled[binding.task_id] = binding.session_configuration_id !== null;
  }
  set({
    catalog,
    taskDrafts,
    taskEnabled,
    taskDirty: emptyTaskFlags(false),
    taskSaving: emptyTaskFlags(false),
    taskErrors: emptyTaskErrors(),
    agentDraft: catalog.agent_defaults,
    agentDirty: false,
    agentSaving: false,
    agentError: null,
    agentConflict: false,
  });
}

/** 把“保留的选择”和“是否启用”收敛成后端实际持久化的绑定值。 */
function effectiveTaskConfiguration(
  state: SettingsState,
  taskId: TaskId,
): string | null {
  return state.taskEnabled[taskId] ? state.taskDrafts[taskId] : null;
}

/** 替换 catalog 中一条连接，兼容新建与编辑。 */
function replaceConnection(
  set: (patch: Partial<SettingsState>) => void,
  get: () => SettingsState,
  connection: Connection,
): void {
  const catalog = get().catalog;
  if (!catalog) return;
  const exists = catalog.connections.some((item) => item.id === connection.id);
  set({
    catalog: {
      ...catalog,
      connections: exists
        ? catalog.connections.map((item) => (item.id === connection.id ? connection : item))
        : [...catalog.connections, connection],
    },
  });
}

/** 创建尚未选择连接和模型的运行配置编辑器。 */
function emptySessionConfigurationEditor(): SessionConfigurationEditorState {
  return {
    configurationId: null,
    version: 0,
    draft: {
      name: "",
      connection_id: "",
      model: "",
      effort: null,
      stable_alias: null,
      agent_callable: false,
    },
    dirty: false,
    saving: false,
    archiving: false,
    error: null,
    conflict: false,
  };
}

/** 把后端运行配置读模型转换为完整替换草稿。 */
function sessionConfigurationEditor(
  configuration: SessionConfiguration,
): SessionConfigurationEditorState {
  return {
    configurationId: configuration.id,
    version: configuration.version,
    draft: {
      name: configuration.name,
      connection_id: configuration.connection_id,
      model: configuration.model,
      effort: configuration.effort,
      stable_alias: configuration.stable_alias ?? null,
      agent_callable: configuration.agent_callable ?? false,
    },
    dirty: false,
    saving: false,
    archiving: false,
    error: null,
    conflict: false,
  };
}

/** 替换 catalog 中一份运行配置，兼容新建与编辑。 */
function replaceSessionConfiguration(
  set: (patch: Partial<SettingsState>) => void,
  get: () => SettingsState,
  configuration: SessionConfiguration,
): void {
  const catalog = get().catalog;
  if (!catalog) return;
  const exists = catalog.session_configurations.some(
    (item) => item.id === configuration.id,
  );
  set({
    catalog: {
      ...catalog,
      session_configurations: exists
        ? catalog.session_configurations.map((item) =>
            item.id === configuration.id ? configuration : item,
          )
        : [...catalog.session_configurations, configuration],
    },
  });
}

/** 在编辑器上记录脱敏错误和冲突状态。 */
function updateEditorError(
  set: (patch: Partial<SettingsState>) => void,
  get: () => SettingsState,
  error: unknown,
  expectedConnectionId: string | null,
  pendingFlag: "saving" | "deleting" | "inheritingRuntimeConfig",
): void {
  const editor = get().connectionEditor;
  if (
    !editor ||
    editor.connectionId !== expectedConnectionId ||
    !editor[pendingFlag]
  ) {
    return;
  }
  set({
    connectionEditor: {
      ...editor,
      saving: false,
      deleting: false,
      inheritingRuntimeConfig: false,
      error: errorMessage(error),
      conflict: isConflict(error),
    },
  });
}

/** 只把凭据命令错误写回发起命令的连接编辑器。 */
function updateEditorErrorForConnection(
  set: (patch: Partial<SettingsState>) => void,
  get: () => SettingsState,
  error: unknown,
  expectedConnectionId: string,
): void {
  const editor = get().connectionEditor;
  if (!editor || editor.connectionId !== expectedConnectionId) return;
  set({
    connectionEditor: {
      ...editor,
      error: errorMessage(error),
      conflict: isConflict(error),
    },
  });
}

/** 合并 secret 脱敏状态，并立即让依赖凭据身份的模型候选失效。 */
function applySecretStatus(
  set: (patch: Partial<SettingsState>) => void,
  get: () => SettingsState,
  kind: SecretKind,
  result: SecretStatusResult,
): void {
  const editor = get().connectionEditor;
  const catalog = get().catalog;
  if (!catalog) return;
  const updatedConnections = catalog.connections.map((connection) => {
    if (connection.id !== result.connection_id) return connection;
    const invalidated = {
      ...connection,
      version: result.version,
      claude_role_models: {},
      codex_catalog: [],
      catalog: {
        ...connection.catalog,
        status: "stale",
        models: [],
        request_identity: null,
        error_code: null,
      },
    };
    return kind === "api_key"
      ? { ...invalidated, auth: { ...connection.auth, status: result.status } }
      : {
          ...invalidated,
          proxy: { ...connection.proxy, password_status: result.status },
        };
  });
  set({
    catalog: { ...catalog, connections: updatedConnections },
    connectionEditor:
      editor?.connectionId === result.connection_id
        ? {
            ...editor,
            version: result.version,
            draft: {
              ...editor.draft,
              claude_role_models: {},
              codex_catalog: [],
              catalog_request_identity: null,
            },
            error: null,
            conflict: false,
            modelFetch: staleModelFetch(editor.modelFetch),
          }
        : editor,
  });
}

/** 模型获取失败后仅同步版本，不覆盖用户尚未保存的草稿。 */
async function syncConnectionVersionAfterFetchFailure(
  api: SettingsApi,
  set: (patch: Partial<SettingsState>) => void,
  get: () => SettingsState,
  connectionId: string,
): Promise<void> {
  try {
    const catalog = await api.fetchCatalog();
    const remote = catalog.connections.find((item) => item.id === connectionId);
    const editor = get().connectionEditor;
    if (!remote || !editor || editor.connectionId !== connectionId) return;
    set({
      catalog,
      connectionEditor: { ...editor, version: remote.version },
    });
  } catch {
    // 原始获取错误已经可见；版本同步失败不覆盖更有用的上游错误。
  }
}

/** 替换一项绑定而不影响其他任务草稿。 */
function replaceTaskBinding(
  set: (patch: Partial<SettingsState>) => void,
  get: () => SettingsState,
  binding: TaskBinding,
): void {
  const catalog = get().catalog;
  if (!catalog) return;
  const exists = catalog.task_bindings.some((item) => item.task_id === binding.task_id);
  set({
    catalog: {
      ...catalog,
      task_bindings: exists
        ? catalog.task_bindings.map((item) =>
            item.task_id === binding.task_id ? binding : item,
          )
        : [...catalog.task_bindings, binding],
    },
  });
}

/** 判断 device-code 登录是否已从未登录或旧账号切换到新账号。 */
function officialLoginCompleted(
  state: ConnectionEditorState["officialAccount"],
  account: CodexOfficialAccount,
): boolean {
  if (!state.login || account.status !== "logged_in") return false;
  if (
    account.login_id === state.login.login_id &&
    account.login_status === "completed"
  ) return true;
  return state.loginBaselineEmail === null || account.email !== state.loginBaselineEmail;
}

/** 将未知异常转换为页面可读的脱敏信息。 */
function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "设置操作失败";
}

/** 判断配置领域的乐观版本冲突。 */
function isConflict(error: unknown): boolean {
  return (
    error instanceof ConfigurationApiError &&
    (error.status === 409 || error.code.includes("VERSION_CONFLICT"))
  );
}
