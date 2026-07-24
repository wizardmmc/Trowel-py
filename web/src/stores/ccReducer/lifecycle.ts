import type { Phase, ReducerState } from "./model";

/**
 * 在 live SSE 干净关闭但没有终态事件时结束 active turn。
 *
 * slash command 可能只返回 status/local_command 后关闭 stream，不会发送
 * finished。transport 失败和用户中断由各自路径处理，此处不能合成 finished
 * 或改写 meta。
 */
export function endActiveTurnOnStreamClose(
  state: ReducerState,
  opts: { aborted: boolean; transportOk: boolean },
): ReducerState {
  if (!opts.transportOk || opts.aborted) {
    return state;
  }

  const last = state.turns[state.turns.length - 1];
  if (!last || last.status !== "active") {
    return state;
  }

  const lastIndex = state.turns.length - 1;
  const turns = state.turns.map((turn, index) =>
    index === lastIndex ? { ...turn, status: "done" as const } : turn,
  );
  return { ...state, turns, phase: "done" };
}

const ACTIVE_PHASES: ReadonlySet<Phase> = new Set([
  "awaiting_first",
  "thinking",
  "generating",
  "tool",
  "retrying",
  "compacting",
  "background_waiting",
]);

/**
 * 将缺少 finished 事件的 history replay 收敛成可继续使用的历史视图。
 *
 * CC 持久化 jsonl 没有 result，回放结束后需要只补齐 active turn 和进行中
 * phase；error/interrupted 等终态保持原样。
 */
export function finalizeHistoryForView(state: ReducerState): ReducerState {
  const turns = state.turns.map((turn) =>
    turn.status === "active"
      ? { ...turn, status: "done" as const }
      : turn,
  );
  const phase: Phase = ACTIVE_PHASES.has(state.phase) ? "done" : state.phase;
  return { ...state, turns, phase };
}
