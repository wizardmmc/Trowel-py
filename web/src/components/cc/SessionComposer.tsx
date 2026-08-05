/** 连接会话状态与通用输入框，并按 runtime capability 开放操作。 */

import type { ModelOption, SlashItem } from "../../api/cc";
import type {
  AgentModel,
  CodexCommand,
} from "../../agent/transport";
import type { PerSessionState } from "../../agent/application";
import {
  filterCodexCommands,
  getRuntimePresentation,
} from "../../agent/runtimes";
import { Composer } from "./Composer";
import { ACTIVE_SESSION_PRESETS, type PermissionPreset } from "./PermissionFactsChip";

interface SessionComposerProps {
  readonly active: PerSessionState | null;
  readonly activeSid: string | null;
  readonly streaming: boolean;
  readonly slashItems: readonly SlashItem[];
  readonly ccModels: readonly ModelOption[];
  readonly codexModels: readonly AgentModel[];
  readonly codexCatalogError: string | null;
  readonly codexCommands: readonly CodexCommand[];
  readonly codexCommandsLoading: boolean;
  readonly codexCommandsError: string | null;
  readonly onRetryCodexCommands: () => void;
  readonly onCodexCommand: (command: CodexCommand, rawText: string) => void;
  readonly onRetryCodexCatalog: () => void;
  readonly onSend: (text: string) => void;
  readonly onInterrupt: () => void;
  readonly onUpdateSettings: (model: string, effort: string) => void;
  readonly onSelectPermissionPreset?: (preset: PermissionPreset) => void;
  readonly onRequestModelPicker: () => void;
  readonly onRequestEffortPicker: () => void;
}

export function SessionComposer({
  active,
  activeSid,
  streaming,
  slashItems,
  ccModels,
  codexModels,
  codexCatalogError,
  codexCommands,
  codexCommandsLoading,
  codexCommandsError,
  onRetryCodexCommands,
  onCodexCommand,
  onRetryCodexCatalog,
  onSend,
  onInterrupt,
  onUpdateSettings,
  onSelectPermissionPreset,
  onRequestModelPicker,
  onRequestEffortPicker,
}: SessionComposerProps) {
  const phase = active?.phase ?? "idle";
  const effort = active?.effort ?? null;
  const meta = active?.meta ?? null;
  const presentation = active
    ? getRuntimePresentation(active.runtime, active.capabilities)
    : null;
  const composerActions = presentation?.composerActions ?? null;
  const usesSlashCommands = composerActions?.modelSelection === "slash_command";
  const usesSessionPatch = composerActions?.modelSelection === "session_patch";
  const usesCodexCommands = composerActions?.slashSource === "codex";
  const usesClaudeRoster = composerActions?.slashSource === "claude_code";
  const visibleCodexCommands =
    presentation && usesCodexCommands
      ? filterCodexCommands(presentation, codexCommands)
      : [];
  const modelAlias = (() => {
    if (!meta?.model) return null;
    const found = ccModels.find(
      (model) =>
        model.value === meta.model || model.real_model === meta.model,
    );
    return found?.value ?? null;
  })();
  const codexModelOptions: readonly ModelOption[] = codexModels.map(
    (model) => ({
      value: model.id,
      label: model.display_name,
      real_model: model.model,
      description: model.description,
      is_default: model.is_default,
    }),
  );
  const codexCurrentModel =
    active?.pendingModel ?? meta?.model ?? null;
  const selectedCodexModel =
    codexModels.find(
      (model) =>
        model.id === codexCurrentModel ||
        model.model === codexCurrentModel,
    ) ??
    codexModels.find((model) => model.is_default) ??
    codexModels[0];
  const codexEfforts = (selectedCodexModel?.supported_efforts ?? []).map(
    (option) => ({
      value: option.value,
      description: option.description,
      isDefault:
        option.value === selectedCodexModel?.default_effort,
    }),
  );
  const codexCurrentEffort = active?.pendingEffort ?? effort;
  const codexSlashItems: readonly SlashItem[] = visibleCodexCommands.map((command) => ({
    name: command.name,
    description: command.description,
    source: "codex",
    type: "command",
    disabled: streaming && !command.available_while_running,
    disabledReason:
      streaming && !command.available_while_running
        ? "当前 turn 结束后可用"
        : null,
  }));

  function pickCodexModel(modelId: string): void {
    const next = codexModels.find((model) => model.id === modelId);
    if (!next) return;
    const nextEffort = next.supported_efforts.some(
      (option) => option.value === codexCurrentEffort,
    )
      ? (codexCurrentEffort as string)
      : next.default_effort;
    onUpdateSettings(next.id, nextEffort);
  }

  return (
    <Composer
      streaming={streaming}
      disabled={
        !activeSid ||
        active?.resourceState !== "connected" ||
        phase === "awaiting_input" ||
        active?.commandPending != null
      }
      awaitingInput={phase === "awaiting_input"}
      onSend={onSend}
      onInterrupt={composerActions?.interrupt ? onInterrupt : undefined}
      slashItems={
        usesClaudeRoster ? slashItems : usesCodexCommands ? codexSlashItems : []
      }
      slashLoading={usesCodexCommands && codexCommandsLoading}
      slashError={usesCodexCommands ? codexCommandsError : null}
      onRetrySlashItems={
        usesCodexCommands ? onRetryCodexCommands : undefined
      }
      onLocalCommand={
        usesCodexCommands
          ? (item, rawText) => {
              const command = visibleCodexCommands.find(
                (candidate) => candidate.name === item.name,
              );
              if (command) onCodexCommand(command, rawText);
            }
          : undefined
      }
      models={
        usesSlashCommands
          ? ccModels
          : usesSessionPatch
            ? codexModelOptions
            : []
      }
      efforts={
        composerActions?.effortSelection === "session_patch"
          ? codexEfforts
          : undefined
      }
      currentModelAlias={
        usesSlashCommands
          ? modelAlias
          : usesSessionPatch
            ? codexCurrentModel
            : null
      }
      currentEffort={
        composerActions?.effortSelection === "slash_command"
          ? effort
          : composerActions?.effortSelection === "session_patch"
            ? codexCurrentEffort
            : null
      }
      onPickModel={
        usesSlashCommands
          ? (value) => onSend(`/model ${value}`)
          : usesSessionPatch
            ? pickCodexModel
            : undefined
      }
      onPickEffort={
        composerActions?.effortSelection === "slash_command"
          ? (value) => onSend(`/effort ${value}`)
          : composerActions?.effortSelection === "session_patch"
            ? (value) => {
                if (selectedCodexModel) {
                  onUpdateSettings(selectedCodexModel.id, value);
                }
              }
            : undefined
      }
      modelCatalogError={usesSessionPatch ? codexCatalogError : null}
      onRetryModelCatalog={
        usesSessionPatch ? onRetryCodexCatalog : undefined
      }
      settingsDisabled={streaming}
      permissionFacts={
        composerActions?.permissionFacts && active
          ? {
              requested: active.permissionPreset ?? null,
              profile: active.effectivePermissionProfile ?? null,
              sandbox: active.effectiveSandbox ?? null,
              approval: active.effectiveApproval ?? null,
              network: active.networkAccess ?? null,
              label: active.permission,
              selectedPreset:
                (active.permissionPreset as PermissionPreset | null) ?? null,
              // 活动会话菜单不含 follow：sticky turn override 后 Follow 没有
              // 确定的恢复语义。
              selectablePresets: ACTIVE_SESSION_PRESETS,
            }
          : null
      }
      onSelectPermissionPreset={
        composerActions?.permissionFacts && onSelectPermissionPreset && !streaming
          ? onSelectPermissionPreset
          : undefined
      }
      onRequestModelPicker={
        usesSlashCommands ? onRequestModelPicker : undefined
      }
      onRequestEffortPicker={
        composerActions?.effortSelection === "slash_command"
          ? onRequestEffortPicker
          : undefined
      }
      memoryEnabled={active?.memoryEnabled ?? null}
      profileEnabled={active?.profileEnabled ?? null}
    />
  );
}
