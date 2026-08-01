/** 收集新会话的目录、runtime、模型、权限和记忆选项。 */

import { useState } from "react";
import { createPortal } from "react-dom";

import type { AgentModel, AgentRuntimeInfo, Runtime } from "../../agent/transport";
import {
  getExpectedRuntimePresentation,
  getRuntimePresentation,
} from "../../agent/runtimes";
import type { ModelOption } from "../../api/cc";
import {
  RUNTIME_OPTIONS,
  runtimeOptionIndex,
} from "./newSessionOptions";
import { RuntimeSelector } from "./RuntimeSelector";
import { RuntimeSettings } from "./RuntimeSettings";
import { SessionPreferences } from "./SessionPreferences";

// CC 与 Codex 使用不同的权限字段；CC 空值表示沿用宿主默认值。
export interface NewSessionConfig {
  readonly runtime: Runtime;
  readonly memory_enabled: boolean;
  readonly profile_enabled: boolean;
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
}: NewSessionDialogProps) {
  const initialRuntime = initialConfig?.runtime ?? "claude_code";
  const initialPresentation = getExpectedRuntimePresentation(initialRuntime);
  const initialPermission =
    initialPresentation.sessionSettings.permission === "codex_preset"
      ? (initialConfig?.permission_preset ?? "follow")
      : (initialConfig?.permission_mode || "bypassPermissions");
  const [runtime, setRuntime] = useState<Runtime>(initialRuntime);
  const [model, setModel] = useState(initialConfig?.model ?? "");
  const [effort, setEffort] = useState(initialConfig?.effort ?? "");
  const [permission, setPermission] = useState(initialPermission);
  const [memory, setMemory] = useState(initialConfig?.memory_enabled ?? true);
  const [profile, setProfile] = useState(initialConfig?.profile_enabled ?? true);
  const [confirmFullAccess, setConfirmFullAccess] = useState(
    initialPermission === "danger-full-access",
  );
  const [fullAccessConfirmed, setFullAccessConfirmed] = useState(false);

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

  const catalogLoading = runtimesState?.status === "loading";
  const catalogError =
    runtimesState?.status === "error" ? runtimesState.error : null;
  const selectedConnected = isConnected(runtime);
  const runtimeCatalogBlocked =
    modelCatalog === "codex" &&
    (codexCatalogError !== null ||
      selectedCodexModel === undefined ||
      !selectedCodexModel.supported_efforts.some(
        (item) => item.value === selectedCodexEffort,
      ));
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
    (permissionSettings === "codex_preset" &&
      selectedPermission === "danger-full-access" &&
      !fullAccessConfirmed);

  // 切换 runtime 时清空旧选择，避免跨 runtime 泄漏配置。
  function selectRuntime(next: Runtime): void {
    if (next === runtime) return;
    setRuntime(next);
    const nextInfo = readyRuntimes?.find(
      (candidate) => candidate.runtime === next,
    );
    const nextPresentation = nextInfo
      ? getRuntimePresentation(next, nextInfo.capabilities)
      : getExpectedRuntimePresentation(next);
    if (nextPresentation.sessionSettings.modelCatalog === "codex") {
      const defaultModel =
        codexModels.find((item) => item.is_default) ?? codexModels[0];
      setModel(defaultModel?.id ?? "");
      setEffort(defaultModel?.default_effort ?? "");
      setPermission(
        nextPresentation.sessionSettings.permission === "codex_preset"
          ? "follow"
          : "",
      );
    } else {
      setModel("");
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

  const visibleModels =
    modelCatalog === "codex"
      ? codexModels.map((item) => ({ value: item.id, label: item.id }))
      : modelCatalog === "claude_code"
        ? [
          { value: "", label: "跟随 settings" },
          ...ccModels.map((item) => ({ value: item.value, label: item.label })),
        ]
        : [];
  const visibleEfforts =
    !presentation.sessionSettings.effort
      ? []
      : modelCatalog === "codex"
      ? (selectedCodexModel?.supported_efforts ?? []).map((item) => ({
          value: item.value,
          label: item.value,
        }))
      : activeOption.efforts;

  function pickModel(nextModel: string): void {
    setModel(nextModel);
    if (modelCatalog !== "codex") return;
    const next = codexModels.find((item) => item.id === nextModel);
    if (!next) return;
    if (!next.supported_efforts.some((item) => item.value === effort)) {
      setEffort(next.default_effort);
    }
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
      memory_enabled: memory,
      profile_enabled: profile,
      model:
        modelCatalog === "codex"
          ? (selectedCodexModel?.id ?? "")
          : modelCatalog === "claude_code"
            ? selectedCcModel
            : "",
      effort:
        !presentation.sessionSettings.effort
          ? ""
          : modelCatalog === "codex"
            ? selectedCodexEffort
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
            onSelect={selectRuntime}
            onRetry={onRetryRuntimes}
          />

          <RuntimeSettings
            creating={creating}
            showModels={modelCatalog !== null}
            showEffort={presentation.sessionSettings.effort}
            showPermission={permissionSettings !== null}
            models={visibleModels}
            selectedModel={
              modelCatalog === "codex"
                ? (selectedCodexModel?.id ?? "")
                : selectedCcModel
            }
            efforts={visibleEfforts}
            selectedEffort={
              modelCatalog === "codex" ? selectedCodexEffort : selectedCcEffort
            }
            permissions={
              permissionSettings === null ? [] : activeOption.permissions
            }
            selectedPermission={selectedPermission}
            modelCatalogError={
              modelCatalog === "codex" ? codexCatalogError : null
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

          {selectedRuntimeInfo && presentation.missingCapabilities.length > 0 && (
            <div className="cc-dialog__diag" role="status">
              能力信息不完整，部分设置和入口已隐藏：
              {presentation.missingCapabilities.join("、")}
            </div>
          )}

          <SessionPreferences
            memoryDescription={presentation.sessionSettings.memoryDescription}
            isolationNote={presentation.sessionSettings.isolationNote}
            memory={memory}
            profile={profile}
            creating={creating}
            onToggleMemory={() => setMemory((value) => !value)}
            onToggleProfile={() => setProfile((value) => !value)}
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
