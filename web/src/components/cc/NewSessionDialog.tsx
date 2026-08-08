/** 收集新会话的目录、runtime、模型、权限和记忆选项。 */

import { useState } from "react";
import { createPortal } from "react-dom";

import type {
  AgentConnectionOption,
  AgentModel,
  AgentRuntimeInfo,
  Runtime,
} from "../../agent/transport";
import {
  getExpectedRuntimePresentation,
  getRuntimePresentation,
} from "../../agent/runtimes";
import type { ModelOption } from "../../api/cc";
import { RUNTIME_OPTIONS, runtimeOptionIndex } from "./newSessionOptions";
import { RuntimeSelector } from "./RuntimeSelector";
import { RuntimeSettings } from "./RuntimeSettings";
import { SessionPreferences } from "./SessionPreferences";

// CC 与 Codex 使用不同的权限字段；CC 空值表示沿用宿主默认值。
export interface NewSessionConfig {
  readonly runtime: Runtime;
  readonly connection_id?: string;
  readonly memory_enabled: boolean;
  readonly profile_enabled: boolean;
  readonly self_enabled?: boolean;
  /** 连接级 MCP capability 尚未开放，创建请求必须显式关闭。 */
  readonly model: string;
  readonly effort: string;
  readonly permission_mode: string;
  readonly permission_preset?:
    "follow" | "read-only" | "workspace-write" | "danger-full-access";
}

export type RuntimesState =
  | { readonly status: "loading" }
  | { readonly status: "ready"; readonly runtimes: readonly AgentRuntimeInfo[] }
  | { readonly status: "error"; readonly error: string };

interface NewSessionDialogProps {
  readonly workdir: string;
  readonly onCreate: (config: NewSessionConfig) => void;
  readonly onCancel: () => void;
  readonly initialConfig?: NewSessionConfig | null;
  // 省略 catalog 时默认两个 runtime 均已连接。
  readonly runtimesState?: RuntimesState;
  readonly onRetryRuntimes?: () => void;
  readonly creating?: boolean;
  readonly error?: string | null;
  readonly ccModels?: readonly ModelOption[];
  readonly codexModels?: readonly AgentModel[];
  readonly codexCatalogError?: string | null;
  readonly onRetryCodexCatalog?: () => void;
  readonly connectionOptions?: readonly AgentConnectionOption[];
  readonly connectionOptionsLoading?: boolean;
  readonly connectionOptionsError?: string | null;
  readonly onRetryConnectionOptions?: () => void;
}

export function NewSessionDialog({
  workdir,
  onCreate,
  onCancel,
  initialConfig = null,
  runtimesState,
  onRetryRuntimes,
  creating = false,
  error = null,
  ccModels = [],
  codexModels = [],
  codexCatalogError = null,
  onRetryCodexCatalog,
  connectionOptions,
  connectionOptionsLoading = false,
  connectionOptionsError = null,
  onRetryConnectionOptions,
}: NewSessionDialogProps) {
  const initialRuntime = initialConfig?.runtime ?? "claude_code";
  const initialPresentation = getExpectedRuntimePresentation(initialRuntime);
  const initialPermission =
    initialPresentation.sessionSettings.permission === "codex_preset"
      ? (initialConfig?.permission_preset ?? "follow")
      : initialConfig?.permission_mode || "bypassPermissions";
  const [runtime, setRuntime] = useState<Runtime>(initialRuntime);
  const [connectionId, setConnectionId] = useState(
    initialConfig?.connection_id ?? "",
  );
  const [model, setModel] = useState(initialConfig?.model ?? "");
  const [effort, setEffort] = useState(initialConfig?.effort ?? "");
  const [permission, setPermission] = useState(initialPermission);
  const [memory, setMemory] = useState(initialConfig?.memory_enabled ?? true);
  const [profile, setProfile] = useState(
    initialConfig?.profile_enabled ?? true,
  );
  const [selfEnabled, setSelfEnabled] = useState(
    initialConfig?.self_enabled ?? true,
  );
  const [confirmFullAccess, setConfirmFullAccess] = useState(
    initialPermission === "danger-full-access",
  );
  const [fullAccessConfirmed, setFullAccessConfirmed] = useState(false);
  const connectionMode = connectionOptions !== undefined;
  const runtimeConnections = (connectionOptions ?? []).filter(
    (connection) => connection.runtime === runtime,
  );
  const selectedConnection =
    runtimeConnections.find((connection) => connection.id === connectionId) ??
    runtimeConnections.find((connection) => connection.available) ??
    runtimeConnections[0];
  const preferredConnectionModel =
    selectedConnection?.models.find(
      (candidate) =>
        candidate.id === selectedConnection.last_session_choice?.model &&
        candidate.available,
    ) ?? selectedConnection?.models.find((candidate) => candidate.available);
  const selectedConnectionModel =
    selectedConnection?.models.find(
      (candidate) => candidate.id === model && candidate.available,
    ) ?? preferredConnectionModel;
  const selectedConnectionEffort = selectedConnectionModel?.efforts.includes(
    effort,
  )
    ? effort
    : selectedConnectionModel?.efforts.includes(
          selectedConnection?.last_session_choice?.effort ?? "",
        )
      ? (selectedConnection?.last_session_choice?.effort ?? "")
      : (selectedConnectionModel?.default_effort ??
        selectedConnectionModel?.efforts[0] ??
        "");

  const defaultCodexModel =
    codexModels.find((item) => item.is_default) ?? codexModels[0];
  // catalog 可能在对话框打开后返回，因此默认值必须从最新 props 派生。
  const selectedCodexModel =
    codexModels.find((item) => item.id === model) ?? defaultCodexModel;
  const selectedCodexEffort = selectedCodexModel?.supported_efforts.some(
    (item) => item.value === effort,
  )
    ? effort
    : (selectedCodexModel?.default_effort ?? "");
  const selectedCcModel =
    model === "" || ccModels.some((item) => item.value === model) ? model : "";

  const readyRuntimes =
    runtimesState?.status === "ready" ? runtimesState.runtimes : null;
  const selectedRuntimeInfo = readyRuntimes?.find(
    (candidate) => candidate.runtime === runtime,
  );
  const presentation = selectedRuntimeInfo
    ? getRuntimePresentation(runtime, selectedRuntimeInfo.capabilities)
    : getExpectedRuntimePresentation(runtime);
  const modelCatalog = presentation.sessionSettings.modelCatalog;
  const permissionSettings = presentation.sessionSettings.permission;
  const isConnected = (rt: Runtime): boolean => {
    if (!readyRuntimes) return true;
    return readyRuntimes.some((r) => r.runtime === rt && r.connected);
  };
  const installHint = (rt: Runtime): string | null =>
    readyRuntimes?.find((candidate) => candidate.runtime === rt)
      ?.install_hint ?? null;

  const catalogLoading = runtimesState?.status === "loading";
  const catalogError =
    runtimesState?.status === "error" ? runtimesState.error : null;
  const selectedConnected = isConnected(runtime);
  const runtimeCatalogBlocked =
    !connectionMode &&
    modelCatalog === "codex" &&
    (codexCatalogError !== null ||
      selectedCodexModel === undefined ||
      !selectedCodexModel.supported_efforts.some(
        (item) => item.value === selectedCodexEffort,
      ));
  const connectionBlocked =
    connectionMode &&
    (connectionOptionsLoading ||
      connectionOptionsError !== null ||
      selectedConnection === undefined ||
      !selectedConnection.available ||
      selectedConnectionModel === undefined ||
      (modelCatalog === "codex" &&
        selectedConnectionModel.efforts.length > 0 &&
        !selectedConnectionEffort));
  const activeOption = RUNTIME_OPTIONS[runtimeOptionIndex(runtime)];
  const selectedPermission = activeOption.permissions.some(
    (option) => option.value === permission,
  )
    ? permission
    : permissionSettings === "claude_mode"
      ? "bypassPermissions"
      : "follow";
  const selectedCcEffort = activeOption.efforts.some(
    (option) => option.value === effort,
  )
    ? effort
    : "";
  const createBlocked =
    creating ||
    !workdir.trim() ||
    catalogLoading ||
    catalogError !== null ||
    !selectedConnected ||
    runtimeCatalogBlocked ||
    connectionBlocked ||
    (permissionSettings === "codex_preset" &&
      selectedPermission === "danger-full-access" &&
      !fullAccessConfirmed);

  // 切换 runtime 时清空旧选择，避免跨 runtime 泄漏配置。
  function selectRuntime(next: Runtime): void {
    if (next === runtime) return;
    setRuntime(next);
    const nextConnection = (connectionOptions ?? []).find(
      (connection) => connection.runtime === next && connection.available,
    );
    setConnectionId(nextConnection?.id ?? "");
    const nextConnectionModel =
      nextConnection?.models.find(
        (candidate) =>
          candidate.id === nextConnection.last_session_choice?.model &&
          candidate.available,
      ) ?? nextConnection?.models.find((candidate) => candidate.available);
    const nextInfo = readyRuntimes?.find(
      (candidate) => candidate.runtime === next,
    );
    const nextPresentation = nextInfo
      ? getRuntimePresentation(next, nextInfo.capabilities)
      : getExpectedRuntimePresentation(next);
    if (nextPresentation.sessionSettings.modelCatalog === "codex") {
      if (connectionMode) {
        setModel(nextConnectionModel?.id ?? "");
        setEffort(
          nextConnection?.last_session_choice?.effort ??
            nextConnectionModel?.default_effort ??
            nextConnectionModel?.efforts[0] ??
            "",
        );
      } else {
        const defaultModel =
          codexModels.find((item) => item.is_default) ?? codexModels[0];
        setModel(defaultModel?.id ?? "");
        setEffort(defaultModel?.default_effort ?? "");
      }
      setPermission(
        nextPresentation.sessionSettings.permission === "codex_preset"
          ? "follow"
          : "",
      );
    } else {
      setModel(connectionMode ? (nextConnectionModel?.id ?? "") : "");
      setEffort("");
      setPermission(
        nextPresentation.sessionSettings.permission === "claude_mode"
          ? "bypassPermissions"
          : "",
      );
    }
    setConfirmFullAccess(false);
    setFullAccessConfirmed(false);
  }

  const visibleModels = connectionMode
    ? (selectedConnection?.models ?? [])
        .filter((item) => item.available)
        .map((item) => ({
          value: item.id,
          label:
            selectedConnection?.runtime === "codex"
              ? item.id
              : (item.display_name ?? item.id),
        }))
    : modelCatalog === "codex"
      ? codexModels.map((item) => ({ value: item.id, label: item.id }))
      : modelCatalog === "claude_code"
        ? [
            { value: "", label: "跟随 settings" },
            ...ccModels.map((item) => ({
              value: item.value,
              label: item.label,
            })),
          ]
        : [];
  const visibleEfforts = !presentation.sessionSettings.effort
    ? []
    : modelCatalog === "codex"
      ? connectionMode
        ? (selectedConnectionModel?.efforts ?? []).map((value) => ({
            value,
            label: value,
          }))
        : (selectedCodexModel?.supported_efforts ?? []).map((item) => ({
            value: item.value,
            label: item.value,
          }))
      : activeOption.efforts;

  function pickModel(nextModel: string): void {
    setModel(nextModel);
    if (connectionMode) {
      const next = selectedConnection?.models.find(
        (item) => item.id === nextModel,
      );
      if (next && !next.efforts.includes(effort)) {
        setEffort(next.default_effort ?? next.efforts[0] ?? "");
      }
      return;
    }
    if (modelCatalog !== "codex") return;
    const next = codexModels.find((item) => item.id === nextModel);
    if (!next) return;
    if (!next.supported_efforts.some((item) => item.value === effort)) {
      setEffort(next.default_effort);
    }
  }

  function pickConnection(nextConnectionId: string): void {
    const next = runtimeConnections.find(
      (connection) => connection.id === nextConnectionId,
    );
    if (!next?.available) return;
    setConnectionId(next.id);
    const nextModel =
      next.models.find(
        (candidate) =>
          candidate.id === next.last_session_choice?.model &&
          candidate.available,
      ) ?? next.models.find((candidate) => candidate.available);
    setModel(nextModel?.id ?? "");
    setEffort(
      next.last_session_choice?.effort ??
        nextModel?.default_effort ??
        nextModel?.efforts[0] ??
        "",
    );
  }

  function pickPermission(nextPermission: string): void {
    if (nextPermission === "danger-full-access") {
      setPermission(nextPermission);
      setConfirmFullAccess(true);
      setFullAccessConfirmed(false);
      return;
    }
    setPermission(nextPermission);
    setConfirmFullAccess(false);
    setFullAccessConfirmed(false);
  }

  function submit(): void {
    const config: NewSessionConfig = {
      runtime,
      ...(connectionMode && selectedConnection
        ? { connection_id: selectedConnection.id }
        : {}),
      memory_enabled: memory,
      profile_enabled: profile,
      self_enabled: selfEnabled,
      model: connectionMode
        ? (selectedConnectionModel?.id ?? "")
        : modelCatalog === "codex"
          ? (selectedCodexModel?.id ?? "")
          : modelCatalog === "claude_code"
            ? selectedCcModel
            : "",
      effort: !presentation.sessionSettings.effort
        ? ""
        : modelCatalog === "codex"
          ? connectionMode
            ? selectedConnectionEffort
            : selectedCodexEffort
          : selectedCcEffort,
      permission_mode:
        permissionSettings === "claude_mode" ? selectedPermission : "",
      permission_preset:
        permissionSettings === "codex_preset"
          ? (selectedPermission as NonNullable<
              NewSessionConfig["permission_preset"]
            >)
          : undefined,
    };
    onCreate(config);
  }

  return createPortal(
    <div className="cc-dialog__backdrop" onClick={onCancel} role="presentation">
      <div
        className="cc-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="新建 Agent 会话"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onCancel();
        }}
      >
        <div className="cc-dialog__head">
          <p className="cc-dialog__title">新建 Agent 会话</p>
          <span className="cc-dialog__workdir" title={workdir}>
            {workdir}
          </span>
        </div>
        <div className="cc-dialog__body">
          <RuntimeSelector
            runtime={runtime}
            creating={creating}
            catalogLoading={catalogLoading}
            catalogError={catalogError}
            isConnected={isConnected}
            installHint={installHint}
            onSelect={selectRuntime}
            onRetry={onRetryRuntimes}
          />

          {connectionMode && (
            <>
              <div className="cc-dialog__section-label">Connection</div>
              {connectionOptionsLoading && (
                <div className="cc-dialog__diag" role="status">
                  正在读取连接…
                </div>
              )}
              {connectionOptionsError && (
                <div className="cc-dialog__diag" role="alert">
                  连接目录不可用：{connectionOptionsError}
                  {onRetryConnectionOptions && (
                    <button
                      type="button"
                      className="cc-dialog__btn"
                      onClick={onRetryConnectionOptions}
                      disabled={creating}
                      style={{ marginLeft: 8 }}
                    >
                      重试
                    </button>
                  )}
                </div>
              )}
              <div className="cc-dialog__option-row">
                {runtimeConnections.map((connection) => (
                  <button
                    key={connection.id}
                    type="button"
                    className={
                      "cc-dialog__option" +
                      (selectedConnection?.id === connection.id
                        ? " cc-dialog__option--selected"
                        : "")
                    }
                    disabled={creating || !connection.available}
                    title={
                      describeUnavailable(connection.disabled_reason) ??
                      undefined
                    }
                    onClick={() => pickConnection(connection.id)}
                  >
                    {connection.name}
                  </button>
                ))}
              </div>
              {runtimeConnections
                .filter(
                  (connection) =>
                    !connection.available &&
                    describeUnavailable(connection.disabled_reason) !== null,
                )
                .map((connection) => (
                  <div
                    key={`${connection.id}-disabled`}
                    className="cc-dialog__diag"
                    role="status"
                  >
                    {connection.name}：
                    {describeUnavailable(connection.disabled_reason)}
                  </div>
                ))}
            </>
          )}

          <RuntimeSettings
            creating={creating}
            showModels={modelCatalog !== null}
            showEffort={presentation.sessionSettings.effort}
            showPermission={permissionSettings !== null}
            models={visibleModels}
            selectedModel={
              connectionMode
                ? (selectedConnectionModel?.id ?? "")
                : modelCatalog === "codex"
                  ? (selectedCodexModel?.id ?? "")
                  : selectedCcModel
            }
            efforts={visibleEfforts}
            selectedEffort={
              modelCatalog === "codex"
                ? connectionMode
                  ? selectedConnectionEffort
                  : selectedCodexEffort
                : selectedCcEffort
            }
            permissions={
              permissionSettings === null ? [] : activeOption.permissions
            }
            selectedPermission={selectedPermission}
            modelCatalogError={
              !connectionMode && modelCatalog === "codex"
                ? codexCatalogError
                : null
            }
            confirmFullAccess={
              permissionSettings === "codex_preset" && confirmFullAccess
            }
            showWorkspaceApprovalNote={
              permissionSettings === "codex_preset" &&
              selectedPermission === "workspace-write"
            }
            onSelectModel={pickModel}
            onSelectEffort={setEffort}
            onSelectPermission={pickPermission}
            onConfirmFullAccess={() => {
              setPermission("danger-full-access");
              setConfirmFullAccess(false);
              setFullAccessConfirmed(true);
            }}
            onRetryCodexCatalog={onRetryCodexCatalog}
          />

          {selectedRuntimeInfo &&
            presentation.missingCapabilities.length > 0 && (
              <div className="cc-dialog__diag" role="status">
                能力信息不完整，部分设置和入口已隐藏：
                {presentation.missingCapabilities.join("、")}
              </div>
            )}

          <SessionPreferences
            memoryDescription={
              connectionMode
                ? "注入 Memory 正文；Memory MCP 尚未验证，本次不挂载。"
                : presentation.sessionSettings.memoryDescription
            }
            isolationNote={presentation.sessionSettings.isolationNote}
            memory={memory}
            profile={profile}
            selfEnabled={selfEnabled}
            creating={creating}
            onToggleMemory={() => setMemory((value) => !value)}
            onToggleProfile={() => setProfile((value) => !value)}
            onToggleSelf={() => setSelfEnabled((value) => !value)}
          />
          {error && (
            <p className="cc-dialog__error" role="alert">
              {error}
            </p>
          )}
          {!workdir.trim() && (
            <p className="cc-dialog__error" role="alert">
              需要先选择工作目录
            </p>
          )}
        </div>
        <div className="cc-dialog__foot">
          <button
            type="button"
            className="cc-dialog__btn"
            onClick={onCancel}
            disabled={creating}
          >
            取消
          </button>
          <button
            type="button"
            className="cc-dialog__btn cc-dialog__btn--primary"
            onClick={submit}
            disabled={createBlocked}
            title={
              !selectedConnected
                ? "选中的 runtime 未连接"
                : catalogLoading
                  ? "正在检查 runtime 连接"
                  : undefined
            }
          >
            {creating ? "创建中…" : `创建 ${presentation.label} 会话`}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** 把后端稳定原因码转换为新建会话里可直接理解的说明。 */
function describeUnavailable(reason: string | null): string | null {
  if (reason === null) return null;
  if (reason === "capability_unavailable" || reason === "capability_unknown") {
    return null;
  }
  const labels: Readonly<Record<string, string>> = {
    auth_missing: "缺少可用凭据",
    catalog_not_ready: "模型目录尚未就绪",
    model_not_selected: "尚未在设置中选择模型",
    model_selection_stale: "已选模型不在当前目录，请到设置中重新选择",
    capability_unsupported: "该模型与强度组合不受支持",
  };
  return labels[reason] ?? reason;
}
