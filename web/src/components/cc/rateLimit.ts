/**
 * Pure helpers for the slice-077 Codex rate-limit banner.
 *
 * Extracted from ``RateLimitBanner.tsx`` so the component file only exports
 * the component (eslint ``react-refresh/only-export-components``). Everything
 * here is a pure function or a constant — no React, no side effects — which
 * keeps the visibility decision and the countdown formatting unit-testable in
 * isolation.
 *
 * Field shapes trace back to the Codex 0.144.0 protocol
 * (``account.rs:518 AccountRateLimitsUpdatedNotification``) via the BE
 * translator; see ``ccTypes.ts::RateLimitSnapshot``.
 */
import type { RateLimitSnapshot, RateLimitWindow } from "../../api/ccTypes";

/** Percent at which a not-yet-reached window starts warning. The protocol has
 * no "approaching" signal — this is a product-decided threshold, deliberately
 * conservative so the user gets a heads-up before the hard limit. */
export const NEAR_THRESHOLD_PERCENT = 80;

/** Chinese display labels for ``RateLimitReachedType`` (account.rs). Unknown
 * future tags fall through to the raw wire value so an unfamiliar limit kind
 * surfaces instead of being hidden as "no limit" (forward-compatible). */
export const REACHED_TYPE_LABEL: Readonly<Record<string, string>> = {
  rate_limit_reached: "常规速率限制触顶",
  workspace_owner_credits_depleted: "工作区额度耗尽（所有者）",
  workspace_member_credits_depleted: "工作区额度耗尽（成员）",
  workspace_owner_usage_limit_reached: "工作区用量上限（所有者）",
  workspace_member_usage_limit_reached: "工作区用量上限（成员）",
};

export type RateLimitLevel = "near" | "reached";

/** True when a single window's used_percent crosses the near threshold.
 * Null / non-numeric usedPercent never triggers (spec C-4: no fabrication). */
function windowNear(win: RateLimitWindow | null): boolean {
  const used = win?.usedPercent;
  return typeof used === "number" && used >= NEAR_THRESHOLD_PERCENT;
}

/** Decide whether the snapshot warrants a banner, and at what level.
 *
 * ``reached`` is account-scoped: any non-null ``rate_limit_reached_type``
 * means a limit fired (regardless of per-window percent). ``near`` fires when
 * EITHER window crosses the threshold — the protocol's primary and secondary
 * windows are independent rate-limit surfaces, so a high secondary must warn
 * even when primary is idle (people-confirmed 2026-07-20 scenario 5 shows both
 * windows painted; the visibility rule follows the same "any window" reading).
 *
 * Returns ``null`` for a null snapshot or a low-usage rolling update — caller
 * renders nothing (spec C-6: capability-driven UI, no cry wolf on the
 * usedPercent:20 fixture). */
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

/** Format a resets_at (unix seconds) as a "+H:MM" / "+Dd Hh" countdown from
 * ``nowMs`` (unix ms). Clamped at 0 so an expired window shows "+0:00" rather
 * than a negative number while the next rolling update is in flight. */
export function formatResetCountdown(resetsAt: number, nowMs: number): string {
  const remainingSec = Math.max(0, Math.floor(resetsAt * 1000 - nowMs) / 1000);
  const days = Math.floor(remainingSec / 86400);
  const hours = Math.floor((remainingSec % 86400) / 3600);
  const mins = Math.floor((remainingSec % 3600) / 60);
  if (days > 0) return `+${days}d ${hours}h`;
  return `+${hours}:${String(mins).padStart(2, "0")}`;
}
