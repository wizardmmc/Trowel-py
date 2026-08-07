/** 连接会话状态与通用输入框，并按 runtime capability 开放操作。 */

import type { ModelOption, SlashItem } from "../../api/cc";
import type {
  AgentModel,
  CodexCommand,
  CodexSkill,
} from "../../agent/transport";
import type { PerSessionState } from "../../agent/application";
import {
  filterCodexCommands,
  getRuntimePresentation,
} from "../../agent/runtimes";
import { Composer } from "./Composer";
import { ACTIVE_SESSION_PRESETS, type PermissionPreset } from "./PermissionFactsChip";

const CODEX_SKILL_SOURCE: Readonly<Record<CodexSkill["scope"], SlashItem["source"]>> = {
  user: "user",
  repo: "project",
  admin: "admin",
  system: "system",
};

/** 根据真实加载状态生成不混淆停用、失败和空目录的技能摘要。 */
function describeCodexSkills(
  skills: readonly CodexSkill[],
  warnings: readonly string[],
): string {
  const enabledUsers = skills.filter(
    (skill) => skill.scope === "user" && skill.enabled,
  ).length;
  const disabledUsers = skills.filter(
    (skill) => skill.scope === "user" && !skill.enabled,
  ).length;
  const disabledSuffix = disabledUsers
    ? `；另有 ${disabledUsers} 个用户技能已停用`
    : "";
  const warningSuffix = warnings.length
    ? `；另有 ${warnings.length} 个配置加载警告`
    : "";
  if (enabledUsers > 0) {
    return `当前连接已加载 ${enabledUsers} 个已启用用户技能${disabledSuffix}${warningSuffix}。`;
  }
  if (disabledUsers > 0) {
    return `当前连接没有已启用的用户技能；${disabledUsers} 个用户技能已停用${warningSuffix}。系统技能、管理员技能和项目技能仍会如实显示。`;
  }
  return `当前连接没有用户技能${warningSuffix}；系统技能、管理员技能和项目技能仍会如实显示。`;
}

interface SessionComposerProps {
  readonly active: PerSessionState | null;
  readonly activeSid: string | null;
  readonly streaming: boolean;
  readonly slashItems: readonly SlashItem[];
  readonly slashLoading: boolean;
  readonly slashError: string | null;
  readonly onRetrySlashItems: () => void;
  readonly ccModels: readonly ModelOption[];
  readonly codexModels: readonly AgentModel[];
  readonly codexCatalogError: string | null;
  readonly codexCommands: readonly CodexCommand[];
  readonly codexCommandsLoading: boolean;
  readonly codexCommandsError: string | null;
  readonly onRetryCodexCommands: () => void;
  readonly codexSkills: readonly CodexSkill[];
  readonly codexSkillsLoading: boolean;
  readonly codexSkillsError: string | null;
  readonly codexSkillWarnings: readonly string[];
  readonly onRetryCodexSkills: () => void;
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
  slashLoading,
  slashError,
  onRetrySlashItems,
  ccModels,
  codexModels,
  codexCatalogError,
  codexCommands,
  codexCommandsLoading,
  codexCommandsError,
  onRetryCodexCommands,
  codexSkills,
  codexSkillsLoading,
  codexSkillsError,
  codexSkillWarnings,
  onRetryCodexSkills,
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
  const usesCodexSkills = presentation?.runtime === "codex";
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
  const codexSkillItems: readonly SlashItem[] = codexSkills.map((skill) => ({
    name: skill.name,
    description: skill.description,
    source: CODEX_SKILL_SOURCE[skill.scope],
    type: "skill",
    disabled: !skill.enabled,
    disabledReason: skill.enabled ? null : "当前 Codex 配置已停用此技能",
  }));
  const claudeUserItemCount = slashItems.filter(
    (item) => item.source === "user",
  ).length;

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
      slashLoading={usesClaudeRoster ? slashLoading : usesCodexCommands && codexCommandsLoading}
      slashError={usesClaudeRoster ? slashError : usesCodexCommands ? codexCommandsError : null}
      onRetrySlashItems={
        usesClaudeRoster
          ? onRetrySlashItems
          : usesCodexCommands
            ? onRetryCodexCommands
            : undefined
      }
      slashContext={
        usesCodexCommands
          ? "斜杠菜单是 Codex 会话命令；要调用技能，请输入 $。"
          : usesClaudeRoster
            ? slashLoading
              ? "正在读取当前 Claude 会话的 skill / command。"
              : slashError
                ? "skill / command 读取失败，暂时无法判断当前会话加载了哪些用户项。"
                : claudeUserItemCount > 0
                  ? `当前会话已加载 ${claudeUserItemCount} 项用户 skill / command。`
                  : "当前会话没有用户 skill / command；内置项和项目项仍会如实显示。"
            : null
      }
      skillItems={usesCodexSkills ? codexSkillItems : undefined}
      skillLoading={usesCodexSkills && codexSkillsLoading}
      skillError={usesCodexSkills ? codexSkillsError : null}
      onRetrySkillItems={usesCodexSkills ? onRetryCodexSkills : undefined}
      skillTriggerEnabled={usesCodexSkills}
      skillContext={
        usesCodexSkills
          ? codexSkillsLoading
            ? "正在读取当前连接的技能目录。"
            : codexSkillsError
              ? "技能目录读取失败，暂时无法判断这项连接加载了哪些技能。"
              : describeCodexSkills(codexSkills, codexSkillWarnings)
          : null
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
