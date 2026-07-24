import type {
  HostStatusEvent,
  RateLimitSnapshot,
  RateLimitUpdatedEvent,
  UsageUpdatedEvent,
} from "../../api/ccTypes";
import type { ReducerState, Turn } from "./model";

export function applyUsageUpdated(
  prev: ReducerState,
  event: UsageUpdatedEvent,
): ReducerState {
  const usage: Readonly<Record<string, unknown>> = {
    total: event.total ?? null,
    last: event.last ?? null,
    model_context_window: event.model_context_window ?? null,
  };
  return { ...prev, meta: { ...prev.meta, usage } };
}

/** host_exited 同时结束当前 turn 并保留 degraded 状态供重连提示使用。 */
export function applyHostStatus(
  prev: ReducerState,
  event: HostStatusEvent,
): ReducerState {
  if (event.status === "host_exited") {
    const turns = prev.turns;
    const meta = { ...prev.meta, hostDegraded: true };
    if (turns.length === 0) {
      return { ...prev, phase: "error", meta };
    }

    const last = turns[turns.length - 1];
    const updated: Turn = { ...last, status: "error" };
    return {
      ...prev,
      phase: "error",
      meta,
      turns: [...turns.slice(0, -1), updated],
    };
  }

  const degraded = event.status === "degraded";
  if (degraded === prev.meta.hostDegraded) return prev;
  return { ...prev, meta: { ...prev.meta, hostDegraded: degraded } };
}

export function applyRateLimitUpdated(
  prev: ReducerState,
  event: RateLimitUpdatedEvent,
): ReducerState {
  // 稀疏字段中的 null 是协议事实，不能伪造成 0 或空对象。
  const rateLimit: RateLimitSnapshot = {
    limit_id: event.limit_id ?? null,
    limit_name: event.limit_name ?? null,
    primary: event.primary ?? null,
    secondary: event.secondary ?? null,
    credits: event.credits ?? null,
    individual_limit: event.individual_limit ?? null,
    spend_control_reached: event.spend_control_reached ?? null,
    plan_type: event.plan_type ?? null,
    rate_limit_reached_type: event.rate_limit_reached_type ?? null,
  };
  return { ...prev, meta: { ...prev.meta, rateLimit } };
}
