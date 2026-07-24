import { useState } from "react";
import { createPortal } from "react-dom";

import type { AgentModel, AgentRuntimeInfo, Runtime } from "../../api/agent";
import type { ModelOption } from "../../api/cc";
import {
  RUNTIME_LABEL,
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
  runtimesState,
  onRetryRuntimes,
  creating = false,
  error = null,
  ccModels = [],
  codexModels = [],
  codexCatalogError = null,
  onRetryCodexCatalog,
}: NewSessionDialogProps) {
  const [runtime, setRuntime] = useState<Runtime>("claude_code");
  const [model, setModel] = useState<string>("");
  const [effort, setEffort] = useState<string>("");
  const [permission, setPermission] = useState<string>("");
  const [memory, setMemory] = useState(true);
  const [profile, setProfile] = useState(true);
  const [confirmFullAccess, setConfirmFullAccess] = useState(false);

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

  const readyRuntimes =
    runtimesState?.status === "ready" ? runtimesState.runtimes : null;
  const isConnected = (rt: Runtime): boolean => {
    if (!readyRuntimes) return true;
    return readyRuntimes.some((r) => r.runtime === rt && r.connected);
  };

  const catalogLoading = runtimesState?.status === "loading";
  const catalogError =
    runtimesState?.status === "error" ? runtimesState.error : null;
  const selectedConnected = isConnected(runtime);
  const codexCatalogBlocked =
    runtime === "codex" &&
    (codexCatalogError !== null ||
      selectedCodexModel === undefined ||
      !selectedCodexModel.supported_efforts.some(
        (item) => item.value === selectedCodexEffort,
      ));
  const createBlocked =
    creating ||
    catalogLoading ||
    catalogError !== null ||
    !selectedConnected ||
    codexCatalogBlocked;

  // 切换 runtime 时清空旧选择，避免跨 runtime 泄漏配置。
  function selectRuntime(next: Runtime): void {
    if (next === runtime) return;
    setRuntime(next);
    if (next === "codex") {
      const defaultModel =
        codexModels.find((item) => item.is_default) ?? codexModels[0];
      setModel(defaultModel?.id ?? "");
      setEffort(defaultModel?.default_effort ?? "");
      setPermission("follow");
    } else {
      setModel("");
      setEffort("");
      setPermission("");
    }
    setConfirmFullAccess(false);
  }

  const activeOption = RUNTIME_OPTIONS[runtimeOptionIndex(runtime)];
  const visibleModels =
    runtime === "codex"
      ? codexModels.map((item) => ({ value: item.id, label: item.id }))
      : [
          { value: "", label: "跟随 settings" },
          ...ccModels.map((item) => ({ value: item.value, label: item.label })),
        ];
  const visibleEfforts =
    runtime === "codex"
      ? (selectedCodexModel?.supported_efforts ?? []).map((item) => ({
          value: item.value,
          label: item.value,
        }))
      : activeOption.efforts;

  function pickModel(nextModel: string): void {
    setModel(nextModel);
    if (runtime !== "codex") return;
    const next = codexModels.find((item) => item.id === nextModel);
    if (!next) return;
    if (!next.supported_efforts.some((item) => item.value === effort)) {
      setEffort(next.default_effort);
    }
  }

  function pickPermission(nextPermission: string): void {
    if (nextPermission === "danger-full-access") {
      setConfirmFullAccess(true);
      return;
    }
    setPermission(nextPermission);
    setConfirmFullAccess(false);
  }

  function submit(): void {
    const config: NewSessionConfig = {
      runtime,
      memory_enabled: memory,
      profile_enabled: profile,
      model: runtime === "codex" ? (selectedCodexModel?.id ?? "") : model,
      effort: runtime === "codex" ? selectedCodexEffort : effort,
      permission_mode: runtime === "claude_code" ? permission : "",
      permission_preset:
        runtime === "codex"
          ? (permission as NonNullable<NewSessionConfig["permission_preset"]>)
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
            runtime={runtime}
            creating={creating}
            models={visibleModels}
            selectedModel={
              runtime === "codex" ? (selectedCodexModel?.id ?? "") : model
            }
            efforts={visibleEfforts}
            selectedEffort={
              runtime === "codex" ? selectedCodexEffort : effort
            }
            permissions={activeOption.permissions}
            selectedPermission={permission}
            codexCatalogError={codexCatalogError}
            confirmFullAccess={confirmFullAccess}
            onSelectModel={pickModel}
            onSelectEffort={setEffort}
            onSelectPermission={pickPermission}
            onConfirmFullAccess={() => {
              setPermission("danger-full-access");
              setConfirmFullAccess(false);
            }}
            onRetryCodexCatalog={onRetryCodexCatalog}
          />

          <SessionPreferences
            runtime={runtime}
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
            {creating ? "创建中…" : `创建 ${RUNTIME_LABEL[runtime]} 会话`}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
