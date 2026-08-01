/** 集中生成会话阶段和顶部摘要的中文展示文案。 */

import type { Phase, SessionMeta } from "../../agent/domain";

export interface StatusCopy {
  readonly phases: Readonly<Record<Phase, string>>;
  readonly effortNames: Readonly<Record<string, string>>;
  readonly waitingForRuntime: (runtimeLabel: string) => string;
  readonly turnTokens: (formattedTokens: string) => string;
  readonly compactions: (count: number) => string;
  readonly effortSummary: (effortName: string) => string;
}

export const STATUS_COPY_ZH_CN: StatusCopy = {
  phases: {
    idle: "空闲",
    awaiting_first: "等待 Agent 接手…",
    thinking: "思考中",
    generating: "生成中",
    tool: "执行工具",
    retrying: "重试中",
    compacting: "正在自动压缩",
    background_waiting: "等待后台任务",
    awaiting_input: "等你回答",
    done: "完成",
    error: "出错",
    interrupted: "已中断",
  },
  effortNames: {
    minimal: "最低",
    low: "低",
    medium: "中",
    high: "高",
    xhigh: "极高",
    max: "最高",
    ultracode: "超强编码",
    auto: "自动",
  },
  waitingForRuntime: (runtimeLabel) => `等待 ${runtimeLabel} 接手…`,
  turnTokens: (formattedTokens) => `本轮 ${formattedTokens} tokens`,
  compactions: (count) => `已压缩 ${count} 次`,
  effortSummary: (effortName) => `${effortName}强度思考`,
};

export function phaseLabel(
  phase: Phase,
  runtimeLabel: string,
  copy: StatusCopy = STATUS_COPY_ZH_CN,
): string {
  return phase === "awaiting_first"
    ? copy.waitingForRuntime(runtimeLabel)
    : copy.phases[phase];
}

export function accountingLabel(
  meta: SessionMeta,
  copy: StatusCopy = STATUS_COPY_ZH_CN,
): string | null {
  const parts: string[] = [];
  if (meta.lastTurnTokens !== null) {
    parts.push(copy.turnTokens(formatTokenCount(meta.lastTurnTokens)));
  }
  if (meta.compactionCount > 0) {
    parts.push(copy.compactions(meta.compactionCount));
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}

export function reasoningEffortLabel(
  effort: string | null,
  copy: StatusCopy = STATUS_COPY_ZH_CN,
): string {
  if (effort === null) return copy.phases.thinking;
  return copy.effortSummary(copy.effortNames[effort] ?? effort);
}

export function formatTokenCount(tokens: number): string {
  if (tokens < 1_000) return String(tokens);
  if (tokens < 1_000_000) return `${compactDecimal(tokens / 1_000)}k`;
  return `${compactDecimal(tokens / 1_000_000)}m`;
}

function compactDecimal(value: number): string {
  return value.toFixed(1).replace(/\.0$/, "");
}
