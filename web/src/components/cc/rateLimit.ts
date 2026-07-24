import type { RateLimitSnapshot, RateLimitWindow } from "../../api/ccTypes";

/** 协议没有“接近上限”信号，80% 是产品侧预警阈值。 */
export const NEAR_THRESHOLD_PERCENT = 80;

/** 未知新枚举由调用方显示原始值，不能误判为无限额。 */
export const REACHED_TYPE_LABEL: Readonly<Record<string, string>> = {
  rate_limit_reached: "常规速率限制触顶",
  workspace_owner_credits_depleted: "工作区额度耗尽（所有者）",
  workspace_member_credits_depleted: "工作区额度耗尽（成员）",
  workspace_owner_usage_limit_reached: "工作区用量上限（所有者）",
  workspace_member_usage_limit_reached: "工作区用量上限（成员）",
};

export type RateLimitLevel = "near" | "reached";

function windowNear(win: RateLimitWindow | null): boolean {
  const used = win?.usedPercent;
  return typeof used === "number" && used >= NEAR_THRESHOLD_PERCENT;
}

/** 任一窗口接近上限即预警；缺失或非法数值不合成状态。 */
export function rateLimitLevel(
  snapshot: RateLimitSnapshot | null,
): RateLimitLevel | null {
  if (!snapshot) return null;
  if (snapshot.rate_limit_reached_type) return "reached";
  if (windowNear(snapshot.primary) || windowNear(snapshot.secondary)) {
    return "near";
  }
  return null;
}

/** 过期时间钳制为零，避免下一次滚动更新前显示负数。 */
export function formatResetCountdown(resetsAt: number, nowMs: number): string {
  const remainingSec = Math.max(0, Math.floor(resetsAt * 1000 - nowMs) / 1000);
  const days = Math.floor(remainingSec / 86400);
  const hours = Math.floor((remainingSec % 86400) / 3600);
  const mins = Math.floor((remainingSec % 3600) / 60);
  if (days > 0) return `+${days}d ${hours}h`;
  return `+${hours}:${String(mins).padStart(2, "0")}`;
}
