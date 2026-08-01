/** 把 Claude 与 Codex 的原始用量字段归一为单轮 token 总数。 */

import type { TokenUsageBreakdown } from "../../transport/events";

const CLAUDE_TOKEN_FIELDS = [
  "input_tokens",
  "cache_creation_input_tokens",
  "cache_read_input_tokens",
  "output_tokens",
] as const;

export function claudeTurnTokens(
  usage: Readonly<Record<string, unknown>> | null | undefined,
): number | null {
  if (!usage) return null;

  let found = false;
  let total = 0;
  for (const field of CLAUDE_TOKEN_FIELDS) {
    const value = nonNegativeNumber(usage[field]);
    if (value === null) continue;
    found = true;
    total += value;
  }
  return found ? total : null;
}

export function codexTurnTokens(
  usage: Readonly<TokenUsageBreakdown> | null | undefined,
): number | null {
  return nonNegativeNumber(usage?.totalTokens);
}

function nonNegativeNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : null;
}
