/** 根据会话 runtime 选择并公开对应的展示适配。 */

import type { Runtime } from "../transport";
import { createClaudeCodePresentation } from "./claude-code";
import { createCodexPresentation } from "./codex";
import type { RuntimePresentation } from "./shared";

export * from "./shared";
export * from "./claude-code";
export * from "./codex";

export function getRuntimePresentation(
  runtime: Runtime,
  capabilities: readonly string[],
): RuntimePresentation {
  return runtime === "claude_code"
    ? createClaudeCodePresentation(capabilities)
    : createCodexPresentation(capabilities);
}

export function getExpectedRuntimePresentation(
  runtime: Runtime,
): RuntimePresentation {
  const empty = getRuntimePresentation(runtime, []);
  return getRuntimePresentation(runtime, empty.expectedCapabilities);
}
