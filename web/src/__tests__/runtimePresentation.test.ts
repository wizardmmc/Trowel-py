import { describe, expect, it } from "vitest";

import {
  getRuntimePresentation,
  type AgentCapability,
} from "../agent/runtimes";

const CC_CAPABILITIES: readonly AgentCapability[] = [
  "tools",
  "models",
  "effort",
  "permission",
  "question",
  "interrupt",
  "slash_commands",
  "workflow",
  "tasks",
  "subagents",
  "checkpoint",
  "revert",
  "mcp",
];

const CODEX_CAPABILITIES: readonly AgentCapability[] = [
  "tools",
  "models",
  "effort",
  "permission",
  "sandbox",
  "network_access",
  "approval",
  "interrupt",
  "slash_commands",
  "goal",
  "plan",
  "review",
  "subagents",
  "turn_diff",
  "mcp",
];

describe("runtime presentation", () => {
  it("keeps Claude Code and Codex as sibling adapters", () => {
    const cc = getRuntimePresentation("claude_code", CC_CAPABILITIES);
    const codex = getRuntimePresentation("codex", CODEX_CAPABILITIES);

    expect(cc.label).toBe("Claude Code");
    expect(cc.shortLabel).toBe("Claude");
    expect(cc.sessionSettings).toEqual({
      modelCatalog: "claude_code",
      effort: true,
      permission: "claude_mode",
      memoryDescription:
        "给模型读你存的记忆：铁律、dictionary 笔记、近期日记，并挂 memory MCP。关掉做无记忆基线。",
      isolationNote:
        "选择 Claude Code 继续使用现有 Claude Host；它与已运行的 Codex 会话互不切换、互不恢复。",
    });
    expect(cc.composerActions).toEqual({
      slashSource: "claude_code",
      modelSelection: "slash_command",
      effortSelection: "slash_command",
      permissionFacts: false,
      interrupt: true,
    });
    expect(cc.sidePanel).toBe("tasks");
    expect(cc.sidePanelSections).toEqual({
      tasks: true,
      goal: false,
      plan: false,
    });
    expect(cc.timelinePresenters.thinkingLabel(undefined, false)).toBe(
      "Thinking",
    );
    expect(cc.timelinePresenters.thinkingLabel(12, true)).toBe(
      "Thought for 12s",
    );

    expect(codex.label).toBe("Codex");
    expect(codex.shortLabel).toBe("Codex");
    expect(codex.sessionSettings).toEqual({
      modelCatalog: "codex",
      effort: true,
      permission: "codex_preset",
      memoryDescription:
        "注入 trowel 记忆并挂 memory MCP；Codex native memories 保持关闭。",
      isolationNote:
        "选择 Codex 只决定当前会话的运行工具，不会改变已运行的 Claude 会话，也不会修改 Claude 切换配置。",
    });
    expect(codex.composerActions).toEqual({
      slashSource: "codex",
      modelSelection: "session_patch",
      effortSelection: "session_patch",
      permissionFacts: true,
      interrupt: true,
    });
    expect(codex.sidePanel).toBe("goal_plan");
    expect(codex.sidePanelSections).toEqual({
      tasks: false,
      goal: true,
      plan: true,
    });
    expect(codex.timelinePresenters.thinkingLabel(undefined, true)).toBe(
      "Thought",
    );
    expect(codex.timelinePresenters.thinkingLabel(12, true)).toBe(
      "Thought for 12s",
    );
  });

  it("intersects declared capabilities with live and history support", () => {
    const cc = getRuntimePresentation("claude_code", CC_CAPABILITIES);
    const codex = getRuntimePresentation("codex", CODEX_CAPABILITIES);

    expect(cc.supports("question", "live")).toBe(true);
    expect(cc.supports("question", "history")).toBe(true);
    expect(cc.supports("interrupt", "history")).toBe(false);
    expect(cc.supports("approval", "live")).toBe(false);

    expect(codex.supports("approval", "live")).toBe(true);
    expect(codex.supports("approval", "history")).toBe(true);
    expect(codex.supports("plan", "history")).toBe(true);
    expect(codex.supports("turn_diff", "history")).toBe(true);
    expect(codex.supports("review", "history")).toBe(false);
    expect(codex.supports("question", "live")).toBe(false);
  });

  it("degrades from the declared roster instead of inferring from runtime", () => {
    const presentation = getRuntimePresentation("codex", ["tools"]);

    expect(presentation.supports("models", "live")).toBe(false);
    expect(presentation.sessionSettings.modelCatalog).toBeNull();
    expect(presentation.sessionSettings.effort).toBe(false);
    expect(presentation.sessionSettings.permission).toBeNull();
    expect(presentation.composerActions.modelSelection).toBeNull();
    expect(presentation.composerActions.slashSource).toBeNull();
    expect(presentation.sidePanel).toBeNull();
    expect(presentation.missingCapabilities).toContain("models");
    expect(presentation.missingCapabilities).toContain("plan");
  });
});
