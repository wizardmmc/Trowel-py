/** 把 Codex capability 和命令能力转换为通用 runtime 展示配置。 */

import {
  buildRuntimePresentation,
  type AgentCapability,
  type RuntimeAdapterDefinition,
  type RuntimePresentation,
} from "../shared";
import type { CodexCommand, CodexCommandAction } from "../../transport";

const BOTH = ["live", "history"] as const;
const LIVE = ["live"] as const;

const COMMAND_CAPABILITIES: Partial<
  Record<CodexCommandAction, AgentCapability>
> = {
  review: "review",
  goal: "goal",
  diff: "turn_diff",
  agent: "subagents",
};

const CODEX_ADAPTER: RuntimeAdapterDefinition = {
  runtime: "codex",
  label: "Codex",
  shortLabel: "Codex",
  capabilities: [
    { capability: "tools", contexts: BOTH },
    { capability: "models", contexts: LIVE },
    { capability: "effort", contexts: LIVE },
    { capability: "permission", contexts: LIVE },
    { capability: "sandbox", contexts: LIVE },
    { capability: "network_access", contexts: LIVE },
    { capability: "approval", contexts: BOTH },
    { capability: "interrupt", contexts: LIVE },
    { capability: "slash_commands", contexts: LIVE },
    { capability: "goal", contexts: BOTH },
    { capability: "plan", contexts: BOTH },
    { capability: "review", contexts: LIVE },
    { capability: "subagents", contexts: BOTH },
    { capability: "turn_diff", contexts: BOTH },
    { capability: "mcp", contexts: BOTH },
  ],
  modelUpdate: "session_patch",
  effortUpdate: "session_patch",
  permissionSettings: "codex_preset",
  memoryDescription:
    "注入 trowel 记忆并挂 memory MCP；Codex native memories 保持关闭。",
  isolationNote:
    "选择 Codex 只决定这个 session 的 runtime，不会改变已运行的 GLM 会话，也不会修改 cc-switch 配置。",
  interruptedHostLabel: "Codex host",
  degradedHostLabel: "Codex host 已断开",
  explorationCommands: true,
  thinkingLabel: (durationSeconds, completed) => {
    const verb = completed ? "Reasoned" : "Reasoning";
    return durationSeconds === undefined
      ? verb
      : `${verb} for ${durationSeconds}s`;
  },
};

export function createCodexPresentation(
  capabilities: readonly string[],
): RuntimePresentation {
  return buildRuntimePresentation(CODEX_ADAPTER, capabilities);
}

export function filterCodexCommands(
  presentation: RuntimePresentation,
  commands: readonly CodexCommand[],
): readonly CodexCommand[] {
  if (presentation.runtime !== "codex") return [];
  return commands.filter((command) => {
    const capability = COMMAND_CAPABILITIES[command.action];
    return capability === undefined || presentation.supports(capability);
  });
}
