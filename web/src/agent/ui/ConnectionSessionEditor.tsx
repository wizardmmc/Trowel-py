/** 复用 Agent 连接目录编辑一组 runtime、模型、权限和上下文条件。 */

import { useState } from "react";
import type {
  AgentConnectionOption,
  PermissionPreset,
  Runtime,
} from "../application";
import {
  CLAUDE_SESSION_EFFORTS,
  defaultConnectionSessionConfig,
  preferredConnectionEffort,
  preferredConnectionModel,
  type ConnectionSessionConfig,
} from "./connectionSessionConfig";
import { getExpectedRuntimePresentation } from "../runtimes";
import { RUNTIME_OPTIONS, runtimeOptionIndex } from "../../components/cc/newSessionOptions";
import { RuntimeSelector } from "../../components/cc/RuntimeSelector";
import { RuntimeSettings } from "../../components/cc/RuntimeSettings";
import { SessionPreferences } from "../../components/cc/SessionPreferences";

interface ConnectionSessionEditorProps {
  readonly value: ConnectionSessionConfig;
  readonly connections: readonly AgentConnectionOption[];
  readonly disabled?: boolean;
  readonly fixedReadOnly?: boolean;
  readonly discussionParticipant?: boolean;
  readonly error?: string | null;
  readonly onRetry?: () => void;
  readonly onChange: (value: ConnectionSessionConfig) => void;
}

/** 展示设置域连接支持的完整会话条件，供普通 Agent 与研讨共同使用。 */
export function ConnectionSessionEditor({
  value,
  connections,
  disabled = false,
  fixedReadOnly = false,
  discussionParticipant = false,
  error = null,
  onRetry,
  onChange,
}: ConnectionSessionEditorProps) {
  const [fullAccessPending, setFullAccessPending] = useState(false);
  const runtimeConnections = connections.filter(
    (item) => item.runtime === value.runtime,
  );
  const connection =
    runtimeConnections.find((item) => item.id === value.connection_id) ??
    runtimeConnections.find((item) => item.available) ??
    runtimeConnections[0];
  const model =
    connection?.models.find(
      (item) => item.id === value.model && item.available,
    ) ?? preferredConnectionModel(connection);
  const supportedEfforts = value.runtime === "claude_code"
    ? CLAUDE_SESSION_EFFORTS
    : (model?.efforts ?? []);
  const effort = supportedEfforts.includes(
    value.effort as (typeof supportedEfforts)[number],
  )
    ? value.effort
    : preferredConnectionEffort(connection, model);
  const presentation = getExpectedRuntimePresentation(value.runtime);
  const runtimeOption = RUNTIME_OPTIONS[runtimeOptionIndex(value.runtime)];
  const permission = fullAccessPending
    ? "danger-full-access"
    : fixedReadOnly
    ? value.runtime === "codex"
      ? "read-only"
      : "dontAsk"
    : value.runtime === "codex"
      ? (value.permission_preset ?? "follow")
      : value.permission_mode || "bypassPermissions";

  function update(patch: Partial<ConnectionSessionConfig>): void {
    onChange({ ...value, ...patch });
  }

  function selectRuntime(runtime: Runtime): void {
    setFullAccessPending(false);
    const next = defaultConnectionSessionConfig(
      connections,
      runtime,
      discussionParticipant ? "discussion" : "agent",
    );
    onChange({
      ...next,
      memory_enabled: value.memory_enabled,
      profile_enabled: value.profile_enabled,
      self_enabled: value.self_enabled,
      ...(fixedReadOnly
        ? {
            permission_mode: runtime === "claude_code" ? "dontAsk" : "",
            permission_preset: runtime === "codex" ? "read-only" : undefined,
          }
        : {}),
    });
  }

  function selectConnection(connectionId: string): void {
    const next = runtimeConnections.find((item) => item.id === connectionId);
    if (!next?.available) return;
    const nextModel = preferredConnectionModel(next);
    update({
      connection_id: next.id,
      model: nextModel?.id ?? "",
      effort: preferredConnectionEffort(next, nextModel),
    });
  }

  function selectModel(modelId: string): void {
    const nextModel = connection?.models.find((item) => item.id === modelId);
    if (!nextModel?.available) return;
    update({
      model: nextModel.id,
      effort: value.runtime === "claude_code" || nextModel.efforts.includes(effort)
        ? effort
        : (nextModel.default_effort ?? nextModel.efforts[0] ?? ""),
    });
  }

  function selectPermission(next: string): void {
    if (fixedReadOnly) return;
    if (value.runtime === "codex" && next === "danger-full-access") {
      setFullAccessPending(true);
      return;
    }
    setFullAccessPending(false);
    if (value.runtime === "codex") {
      update({ permission_preset: next as PermissionPreset });
    } else {
      update({ permission_mode: next });
    }
  }

  return (
    <div className="connection-session-editor">
      <RuntimeSelector
        runtime={value.runtime}
        creating={disabled}
        catalogLoading={false}
        catalogError={error}
        isConnected={(runtime) =>
          connections.some((item) => item.runtime === runtime && item.available)
        }
        installHint={() => "没有可用连接"}
        onSelect={selectRuntime}
        onRetry={onRetry}
      />

      <div className="cc-dialog__section-label">Connection</div>
      <div className="cc-dialog__option-row cc-dialog__option-row--connection">
        {runtimeConnections.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`cc-dialog__option cc-dialog__option--connection${connection?.id === item.id ? " cc-dialog__option--selected" : ""}`}
            disabled={disabled || !item.available}
            title={item.disabled_reason ?? undefined}
            onClick={() => selectConnection(item.id)}
          >
            {item.name}
          </button>
        ))}
      </div>

      <RuntimeSettings
        creating={disabled}
        showModels
        showEffort={Boolean(model && supportedEfforts.length > 0)}
        showPermission={!fixedReadOnly}
        models={(connection?.models ?? [])
          .filter((item) => item.available)
          .map((item) => ({
            value: item.id,
            label: item.display_name ?? item.id,
          }))}
        selectedModel={model?.id ?? ""}
        efforts={supportedEfforts.map((item) => ({
          value: item,
          label: item || "跟随",
        }))}
        selectedEffort={effort}
        permissions={runtimeOption.permissions}
        selectedPermission={permission}
        modelCatalogError={null}
        confirmFullAccess={fullAccessPending}
        showWorkspaceApprovalNote={
          !fixedReadOnly && !discussionParticipant && permission === "workspace-write"
        }
        onSelectModel={selectModel}
        onSelectEffort={(next) => update({ effort: next })}
        onSelectPermission={selectPermission}
        onConfirmFullAccess={() => {
          setFullAccessPending(false);
          update({ permission_preset: "danger-full-access" });
        }}
      />

      {fixedReadOnly && (
        <div className="cc-dialog__diag" role="status">
          Permission 固定为 read-only；Claude Code 使用不自动批准写操作的 dontAsk。
        </div>
      )}

      {discussionParticipant && permission === "workspace-write" && (
        <div className="cc-dialog__diag" role="status">
          Workspace 内工具可按沙箱边界运行；需要额外人工审批的操作会在本轮拒绝。
          确实需要放宽边界时可改用 Full access。
        </div>
      )}

      <SessionPreferences
        memoryDescription="注入 Trowel Memory；Memory MCP 是否挂载沿用连接能力门禁。"
        isolationNote={
          discussionParticipant
            ? "参与者不会挂载 Agent 委派 MCP，也不会单独进入 Daily 或 Profile 提炼来源；需要人工批准的操作会在研讨内拒绝。"
            : fixedReadOnly
            ? "该会话使用固定只读权限。"
            : presentation.sessionSettings.isolationNote
        }
        memory={value.memory_enabled}
        profile={value.profile_enabled}
        selfEnabled={value.self_enabled}
        creating={disabled}
        onToggleMemory={() => update({ memory_enabled: !value.memory_enabled })}
        onToggleProfile={() => update({ profile_enabled: !value.profile_enabled })}
        onToggleSelf={() => update({ self_enabled: !value.self_enabled })}
      />
    </div>
  );
}
