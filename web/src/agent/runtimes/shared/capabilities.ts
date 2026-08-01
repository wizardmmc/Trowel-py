/** 定义两个 runtime 共用的 capability 词表和前端展示契约。 */

import type { Runtime } from "../../transport";

export type AgentCapability =
  | "tools"
  | "models"
  | "effort"
  | "permission"
  | "sandbox"
  | "network_access"
  | "approval"
  | "question"
  | "interrupt"
  | "slash_commands"
  | "workflow"
  | "tasks"
  | "goal"
  | "plan"
  | "review"
  | "subagents"
  | "turn_diff"
  | "checkpoint"
  | "revert"
  | "mcp";

export type CapabilityContext = "live" | "history";

/** 说明一项能力在哪些会话场景可用。 */
export interface CapabilityDefinition {
  readonly capability: AgentCapability;
  readonly contexts: readonly CapabilityContext[];
}

export type ModelCatalogKind = Runtime | null;
export type PermissionSettingsKind =
  | "claude_mode"
  | "codex_preset"
  | null;
export type SettingUpdateKind = "slash_command" | "session_patch" | null;
export type SidePanelKind = "tasks" | "goal_plan" | null;

/** 通用会话界面读取的 runtime 能力组合和展示配置。 */
export interface RuntimePresentation {
  readonly runtime: Runtime;
  readonly label: string;
  readonly shortLabel: string;
  readonly expectedCapabilities: readonly AgentCapability[];
  readonly declaredCapabilities: readonly AgentCapability[];
  readonly missingCapabilities: readonly AgentCapability[];
  readonly sessionSettings: {
    readonly modelCatalog: ModelCatalogKind;
    readonly effort: boolean;
    readonly permission: PermissionSettingsKind;
    readonly memoryDescription: string;
    readonly isolationNote: string;
  };
  readonly composerActions: {
    readonly slashSource: Runtime | null;
    readonly modelSelection: SettingUpdateKind;
    readonly effortSelection: SettingUpdateKind;
    readonly permissionFacts: boolean;
    readonly interrupt: boolean;
  };
  readonly headerStatus: {
    readonly interruptedHostLabel: string;
    readonly degradedHostLabel: string | null;
  };
  readonly timelinePresenters: {
    readonly groupExplorationCommands: boolean;
    readonly thinkingLabel: (
      durationSeconds: number | undefined,
      completed: boolean,
    ) => string;
  };
  readonly sidePanel: SidePanelKind;
  readonly sidePanelSections: {
    readonly tasks: boolean;
    readonly goal: boolean;
    readonly plan: boolean;
  };
  readonly supports: (
    capability: AgentCapability,
    context?: CapabilityContext,
  ) => boolean;
}

/** 描述一个 runtime 的预期能力及其对应的交互差异。 */
export interface RuntimeAdapterDefinition {
  readonly runtime: Runtime;
  readonly label: string;
  readonly shortLabel: string;
  readonly capabilities: readonly CapabilityDefinition[];
  readonly modelUpdate: Exclude<SettingUpdateKind, null>;
  readonly effortUpdate: Exclude<SettingUpdateKind, null>;
  readonly permissionSettings: Exclude<PermissionSettingsKind, null>;
  readonly memoryDescription: string;
  readonly isolationNote: string;
  readonly interruptedHostLabel: string;
  readonly degradedHostLabel: string | null;
  readonly explorationCommands: boolean;
  readonly thinkingLabel: RuntimePresentation["timelinePresenters"]["thinkingLabel"];
}

const KNOWN_CAPABILITIES: ReadonlySet<string> = new Set<AgentCapability>([
  "tools",
  "models",
  "effort",
  "permission",
  "sandbox",
  "network_access",
  "approval",
  "question",
  "interrupt",
  "slash_commands",
  "workflow",
  "tasks",
  "goal",
  "plan",
  "review",
  "subagents",
  "turn_diff",
  "checkpoint",
  "revert",
  "mcp",
]);

export function isAgentCapability(value: string): value is AgentCapability {
  return KNOWN_CAPABILITIES.has(value);
}

export function buildRuntimePresentation(
  adapter: RuntimeAdapterDefinition,
  rawCapabilities: readonly string[],
): RuntimePresentation {
  const declaredCapabilities = rawCapabilities.filter(isAgentCapability);
  const declared = new Set(declaredCapabilities);
  const expectedCapabilities = adapter.capabilities.map(
    (definition) => definition.capability,
  );
  const definitions = new Map(
    adapter.capabilities.map((definition) => [
      definition.capability,
      definition.contexts,
    ]),
  );
  const supports = (
    capability: AgentCapability,
    context: CapabilityContext = "live",
  ): boolean =>
    declared.has(capability) &&
    (definitions.get(capability)?.includes(context) ?? false);
  const modelSelection = supports("models") ? adapter.modelUpdate : null;
  const effortSelection = supports("effort") ? adapter.effortUpdate : null;
  const permissionSettings = supports("permission")
    ? adapter.permissionSettings
    : null;
  const sidePanelSections = {
    tasks: supports("tasks"),
    goal: supports("goal"),
    plan: supports("plan"),
  };
  const sidePanel = sidePanelSections.tasks
    ? "tasks"
    : sidePanelSections.goal || sidePanelSections.plan
      ? "goal_plan"
      : null;

  return {
    runtime: adapter.runtime,
    label: adapter.label,
    shortLabel: adapter.shortLabel,
    expectedCapabilities,
    declaredCapabilities,
    missingCapabilities: expectedCapabilities.filter(
      (capability) => !declared.has(capability),
    ),
    sessionSettings: {
      modelCatalog: supports("models") ? adapter.runtime : null,
      effort: supports("effort"),
      permission: permissionSettings,
      memoryDescription: adapter.memoryDescription,
      isolationNote: adapter.isolationNote,
    },
    composerActions: {
      slashSource: supports("slash_commands") ? adapter.runtime : null,
      modelSelection,
      effortSelection,
      permissionFacts:
        permissionSettings === "codex_preset" &&
        (supports("sandbox") ||
          supports("network_access") ||
          supports("approval")),
      interrupt: supports("interrupt"),
    },
    headerStatus: {
      interruptedHostLabel: adapter.interruptedHostLabel,
      degradedHostLabel: adapter.degradedHostLabel,
    },
    timelinePresenters: {
      groupExplorationCommands:
        adapter.explorationCommands && supports("tools"),
      thinkingLabel: adapter.thinkingLabel,
    },
    sidePanel,
    sidePanelSections,
    supports,
  };
}
