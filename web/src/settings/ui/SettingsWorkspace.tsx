/** 组装设置二级导航、状态容器、六组纯展示页面和平台操作。 */

import { useEffect, useState } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import { copyText } from "../../lib/copyText";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { getPlatform } from "../../platform";
import { useNotificationStore } from "../../stores/notificationStore";
import { settingsStore, type SettingsState } from "../application/store";
import type { SecretKind } from "../domain/types";
import { AboutPanel } from "./AboutPanel";
import { AgentDefaultsPanel } from "./AgentDefaultsPanel";
import { ConnectionsPanel } from "./ConnectionsPanel";
import { DiagnosticsPanel } from "./DiagnosticsPanel";
import { PathsPanel } from "./PathsPanel";
import { RuntimeConfigurationsPanel } from "./RuntimeConfigurationsPanel";
import { SettingsSidebar } from "./SettingsSidebar";
import { TaskBindingsPanel } from "./TaskBindingsPanel";
import { SETTINGS_SECTIONS } from "./sectionMetadata";
import "./settings-workspace.css";

export interface SettingsWorkspaceProps {
  readonly store?: StoreApi<SettingsState>;
  readonly active?: boolean;
}

/** 订阅设置 owner，并把 I/O 回调下发给不直接接触 store 的页面。 */
export function SettingsWorkspace({
  store = settingsStore,
  active = true,
}: SettingsWorkspaceProps) {
  const state = useStore(store);
  const addNotification = useNotificationStore((item) => item.addNotification);
  const platform = getPlatform();
  const [connectionToDelete, setConnectionToDelete] = useState<string | null>(null);
  const [configurationToArchive, setConfigurationToArchive] = useState<string | null>(null);
  const [connectionToInherit, setConnectionToInherit] = useState<{
    readonly name: string;
    readonly runtime: "claude_code" | "codex";
  } | null>(null);

  useEffect(() => {
    if (active) void store.getState().initialize();
  }, [active, store]);

  useEffect(() => {
    if (active && state.activeSection === "diagnostics") {
      void store.getState().refreshDiagnostics();
    }
  }, [active, state.activeSection, store]);

  useEffect(() => {
    const editor = state.connectionEditor;
    if (
      editor?.connectionId &&
      editor.draft.kind === "codex_official" &&
      editor.officialAccount.status === "idle"
    ) {
      void store.getState().refreshCodexOfficialAccount();
    }
  }, [state.connectionEditor, store]);

  useEffect(() => {
    const editor = state.connectionEditor;
    if (
      !editor?.connectionId ||
      editor.draft.kind !== "codex_official" ||
      !editor.officialAccount.login
    ) return;
    const timer = window.setInterval(() => {
      void store.getState().refreshCodexOfficialAccount();
    }, 2_000);
    return () => window.clearInterval(timer);
  }, [state.connectionEditor, store]);

  const revealPath = async (path: string) => {
    try {
      await platform.revealPath(path, path);
    } catch {
      addNotification("系统未能打开这个位置", "warning");
    }
  };
  const copyPath = async (path: string) => {
    try {
      await copyText(path);
      addNotification("路径已复制", "success");
    } catch {
      addNotification("路径复制失败", "warning");
    }
  };
  const reloadConnection = async () => {
    const id = store.getState().connectionEditor?.connectionId;
    const reloaded = await store.getState().reloadCatalog();
    if (reloaded && id) store.getState().openConnection(id);
  };
  const deleteCurrentConnection = () => {
    const name = store.getState().connectionEditor?.draft.name || "这条连接";
    setConnectionToDelete(name);
  };
  const inheritCurrentRuntimeConfig = () => {
    const editor = store.getState().connectionEditor;
    if (!editor || editor.draft.runtime === "direct_api") return;
    setConnectionToInherit({
      name: editor.draft.name || "这条连接",
      runtime: editor.draft.runtime,
    });
  };
  const archiveCurrentRuntimeConfiguration = () => {
    const name = store.getState().configurationEditor?.draft.name || "这份运行配置";
    setConfigurationToArchive(name);
  };
  const reloadTask = async (taskId: Parameters<SettingsState["reloadTask"]>[0]) => {
    const reloaded = await store.getState().reloadCatalog();
    if (reloaded) store.getState().reloadTask(taskId);
  };
  const reloadAgentDefaults = async () => {
    const reloaded = await store.getState().reloadCatalog();
    if (reloaded) store.getState().reloadAgentDefaults();
  };
  const writeConnectionSecret = async (kind: SecretKind, value: string) => {
    await store.getState().writeConnectionSecret(kind, value);
    const editor = store.getState().connectionEditor;
    addNotification(editor?.error ? "凭据保存失败" : "凭据状态已更新", editor?.error ? "warning" : "success");
  };
  const beginCodexOfficialLogin = async () => {
    const login = await store.getState().beginCodexOfficialLogin();
    if (!login) {
      addNotification("Codex 登录未能启动", "warning");
      return;
    }
    try {
      await platform.openExternal(login.verification_url);
    } catch {
      addNotification("登录页面未能自动打开，可使用页面中的地址和验证码", "warning");
    }
  };
  const activeSectionTitle = SETTINGS_SECTIONS.find(
    (section) => section.id === state.activeSection,
  )?.label;
  const sidebarStatus = state.loading || !state.initialized
    ? "loading"
    : state.error && !state.catalog
      ? "error"
      : "ready";

  return (
    <div className="settings-workspace">
      <div className="settings-drag-region" aria-hidden="true">Trowel</div>
      <div className="settings-tool-layout">
        <SettingsSidebar
          activeSection={state.activeSection}
          status={sidebarStatus}
          onSectionChange={state.setActiveSection}
        />
        <section className="settings-main-surface">
          <header className="settings-surface-topbar">
            <strong>{activeSectionTitle}</strong>
            <span>每项 Agent 连接拥有独立配置家；已有会话继续使用创建时冻结的配置</span>
          </header>
          <main className="settings-detail">
            {state.loading && !state.catalog ? (
              <div className="settings-page-state" role="status">正在读取设置…</div>
            ) : state.error && !state.catalog ? (
              <div className="settings-page-state is-error" role="alert">
                <strong>设置读取失败</strong>
                <span>{state.error}</span>
                <button type="button" className="settings-button" onClick={() => void state.initialize()}>重新读取</button>
              </div>
            ) : (
              <>
                {state.activeSection === "paths" && (
                  <PathsPanel
                    paths={state.paths}
                    error={state.pathsError}
                    browserMode={platform.environment === "browser"}
                    onCopy={(path) => void copyPath(path)}
                    onReveal={(path) => void revealPath(path)}
                    onRefresh={() => void state.refreshPaths()}
                  />
                )}
                {state.activeSection === "connections" && (
                  <ConnectionsPanel
                    catalog={state.catalog}
                    editor={state.connectionEditor}
                    runtimeFilter={state.connectionRuntimeFilter}
                    onRuntimeFilterChange={state.setConnectionRuntimeFilter}
                    onOpen={state.openConnection}
                    onCreate={state.createConnectionDraft}
                    onNewKindChange={state.changeNewConnectionKind}
                    onClose={state.closeConnection}
                    onDraftChange={state.updateConnectionDraft}
                    onRoleChange={state.updateClaudeRole}
                    onSave={() => void state.saveConnection()}
                    onDelete={deleteCurrentConnection}
                    onInheritRuntimeConfig={inheritCurrentRuntimeConfig}
                    onFetchModels={() => void state.fetchConnectionModels()}
                    onWriteSecret={writeConnectionSecret}
                    onDeleteSecret={(kind) => void state.deleteConnectionSecret(kind)}
                    onStartOfficialLogin={() => void beginCodexOfficialLogin()}
                    onOpenOfficialLogin={(url) => void platform.openExternal(url)}
                    onRefreshOfficialAccount={() => void state.refreshCodexOfficialAccount()}
                    onReload={() => void reloadConnection()}
                  />
                )}
                {state.activeSection === "tasks" && (
                  <TaskBindingsPanel
                    catalog={state.catalog}
                    drafts={state.taskDrafts}
                    enabled={state.taskEnabled}
                    saving={state.taskSaving}
                    errors={state.taskErrors}
                    onChange={(taskId, configurationId) => {
                      store.getState().setTaskDraft(taskId, configurationId);
                      if (store.getState().taskEnabled[taskId]) {
                        void store.getState().saveTask(taskId);
                      }
                    }}
                    onToggle={(taskId, enabled) => {
                      store.getState().setTaskEnabled(taskId, enabled);
                      void store.getState().saveTask(taskId);
                    }}
                    onRetry={(taskId) => void store.getState().saveTask(taskId)}
                    onReload={(taskId) => void reloadTask(taskId)}
                  />
                )}
                {state.activeSection === "configurations" && (
                  <RuntimeConfigurationsPanel
                    catalog={state.catalog}
                    editor={state.configurationEditor}
                    onOpen={state.openSessionConfiguration}
                    onCreate={state.createSessionConfigurationDraft}
                    onClose={state.closeSessionConfiguration}
                    onChange={state.updateSessionConfigurationDraft}
                    onSave={() => void state.saveSessionConfiguration()}
                    onArchive={archiveCurrentRuntimeConfiguration}
                  />
                )}
                {state.activeSection === "agent" && (
                  <AgentDefaultsPanel
                    catalog={state.catalog}
                    draft={state.agentDraft}
                    dirty={state.agentDirty}
                    saving={state.agentSaving}
                    error={state.agentError}
                    conflict={state.agentConflict}
                    onChange={(patch) => {
                      store.getState().updateAgentDraft(patch);
                      void store.getState().saveAgentDefaults();
                    }}
                    onRetry={() => void store.getState().saveAgentDefaults()}
                    onReload={() => void reloadAgentDefaults()}
                  />
                )}
                {state.activeSection === "diagnostics" && (
                  <DiagnosticsPanel
                    diagnostics={state.diagnostics}
                    error={state.diagnosticsError}
                    fetchedAt={state.diagnosticsFetchedAt}
                    onRefresh={() => void state.refreshDiagnostics()}
                  />
                )}
                {state.activeSection === "about" && (
                  <AboutPanel
                    version={platform.appVersion}
                    environment={platform.environment}
                  />
                )}
              </>
            )}
          </main>
        </section>
      </div>
      {connectionToDelete && (
        <ConfirmDialog
          title={`删除“${connectionToDelete}”？`}
          description="相关凭据也会一并删除，此操作无法撤销。"
          confirmLabel="删除连接"
          tone="danger"
          onConfirm={() => {
            setConnectionToDelete(null);
            void store.getState().removeConnection();
          }}
          onCancel={() => setConnectionToDelete(null)}
        />
      )}
      {configurationToArchive && (
        <ConfirmDialog
          title={`归档“${configurationToArchive}”？`}
          description="现有引用会保留为失效状态，新的 Agent、委派和后台任务不会回退到其他配置；已经创建的会话继续使用冻结快照。"
          confirmLabel="归档配置"
          tone="danger"
          onConfirm={() => {
            setConfigurationToArchive(null);
            void store.getState().archiveSessionConfiguration();
          }}
          onCancel={() => setConfigurationToArchive(null)}
        />
      )}
      {connectionToInherit && (
        <ConfirmDialog
          title={`复制全局配置到“${connectionToInherit.name}”？`}
          description={
            connectionToInherit.runtime === "codex"
              ? "将覆盖该连接上次复制的 config.toml、AGENTS.md、rules 和两处 skills。不会复制登录态、会话、SQLite、日志或插件缓存。正在使用该连接的会话需要先关闭；复制完成后是独立副本。"
              : "将覆盖该连接上次复制的 skills、commands、agents、rules、输出样式、CLAUDE.md 和 settings。settings.env 不会复制；同连接的在跑会话可能热加载变化。"
          }
          confirmLabel="确认复制"
          onConfirm={() => {
            const runtime = connectionToInherit.runtime;
            setConnectionToInherit(null);
            void store.getState().inheritRuntimeConfig().then((succeeded) => {
              addNotification(
                succeeded
                  ? `${runtime === "codex" ? "Codex" : "Claude"} 配置已复制`
                  : `${runtime === "codex" ? "Codex" : "Claude"} 配置复制失败`,
                succeeded ? "success" : "warning",
              );
            });
          }}
          onCancel={() => setConnectionToInherit(null)}
        />
      )}
    </div>
  );
}
