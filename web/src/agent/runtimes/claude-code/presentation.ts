/** 把 Claude Code 的 capability 组合转换为通用 runtime 展示配置。 */

import {
  buildRuntimePresentation,
  type RuntimeAdapterDefinition,
  type RuntimePresentation,
} from "../shared";

const BOTH = ["live", "history"] as const;
const LIVE = ["live"] as const;

const CLAUDE_CODE_ADAPTER: RuntimeAdapterDefinition = {
  runtime: "claude_code",
  label: "Claude Code",
  shortLabel: "CC",
  capabilities: [
    { capability: "tools", contexts: BOTH },
    { capability: "models", contexts: LIVE },
    { capability: "effort", contexts: LIVE },
    { capability: "permission", contexts: LIVE },
    { capability: "question", contexts: BOTH },
    { capability: "interrupt", contexts: LIVE },
    { capability: "slash_commands", contexts: LIVE },
    { capability: "workflow", contexts: BOTH },
    { capability: "tasks", contexts: BOTH },
    { capability: "subagents", contexts: BOTH },
    { capability: "checkpoint", contexts: BOTH },
    { capability: "revert", contexts: BOTH },
    { capability: "mcp", contexts: BOTH },
  ],
  modelUpdate: "slash_command",
  effortUpdate: "slash_command",
  permissionSettings: "claude_mode",
  memoryDescription:
    "给模型读你存的记忆：铁律、dictionary 笔记、近期日记，并挂 memory MCP。关掉做无记忆基线。",
  isolationNote:
    "选择 Claude Code 继续使用 CCHost；它与已运行的 Codex session 互不切换、互不 resume。",
  interruptedHostLabel: "CC 进程",
  degradedHostLabel: null,
  explorationCommands: false,
  thinkingLabel: (durationSeconds) =>
    durationSeconds === undefined ? "思考" : `Thought for ${durationSeconds}s`,
};

export function createClaudeCodePresentation(
  capabilities: readonly string[],
): RuntimePresentation {
  return buildRuntimePresentation(CLAUDE_CODE_ADAPTER, capabilities);
}
