/**
 * RateLimitBanner — slice-077 Codex account rate-limit UI.
 *
 * Source of truth: docs/design/front-end/milestone-9-codex-advanced-events-
 * 20260720.html scenario 5 (people-confirmed 2026-07-20). Decision 5: the UI
 * unfolds only ``used_percent`` / ``resets_at`` / ``rate_limit_reached_type``.
 * ``credits`` / ``individual_limit`` / ``spend_control_reached`` stay in the
 * reducer payload for a later UI pass but are never thrown away (spec C-4).
 *
 * Visibility (spec "恢复后状态清除" + decision 5):
 *   * ``rate_limit_reached_type != null`` → reached (danger).
 *   * else any window's ``used_percent >= NEAR_THRESHOLD_PERCENT`` → near
 *     (sunshine). The protocol's primary and secondary windows are
 *     independent surfaces — a high secondary warns even when primary is idle.
 *   * else → render nothing. The fixture's usedPercent:20 rolling update must
 *     NOT pop a banner (capability-driven UI, spec C-6).
 *
 * The mockup's green "recovered" state is intentionally NOT implemented:
 * reached → not-reached simply hides the banner, which is exactly the spec's
 * "恢复后状态清除" contract. A transient recovered card would need a timer
 * and a previous-state flag for no observed benefit.
 *
 * Account-scoped event (no per-thread binding): the BE fans it out to every
 * active Codex session, so every Codex session renders its own banner from the
 * same snapshot. CC never receives this event.
 *
 * Countdown freshness: reading the clock is impure, so render never calls
 * ``Date.now()`` directly. A module-level tick source (subscribe / snapshot)
 * caches the current ms and refreshes once a minute; ``useSyncExternalStore``
 * reads the cached value during render (pure). The banner is split into an
 * outer gate (decides visibility) and an inner content component that only
 * mounts when a banner is actually shown — so idle sessions never start the
 * shared tick timer.
 */
import { useSyncExternalStore } from "react";
import type { RateLimitSnapshot, RateLimitWindow } from "../../api/ccTypes";
import {
  NEAR_THRESHOLD_PERCENT,
  REACHED_TYPE_LABEL,
  formatResetCountdown,
  rateLimitLevel,
} from "./rateLimit";

/** Refresh cadence for the resets_at countdown. Seconds-level precision adds
 * noise to a "you're rate-limited, wait 12 hours" message; one minute is close
 * enough and keeps the timer cheap. */
const COUNTDOWN_TICK_MS = 60_000;

// ── Module-level tick source ───────────────────────────────────────────────
// A single shared interval drives every mounted banner. Reference-counted:
// the first subscriber refreshes the cache and starts the timer; the last
// unsubscribe stops it. ``cachedNow`` is the only value ``getNowSnapshot``
// ever returns, so React sees render as pure (no ``Date.now()`` in render).
let cachedNow = Date.now();
const tickSubscribers = new Set<() => void>();
let tickIntervalId: ReturnType<typeof setInterval> | null = null;

function subscribeTick(callback: () => void): () => void {
  tickSubscribers.add(callback);
  if (tickIntervalId === null) {
    // Refresh on first subscribe so a banner that mounts after a long idle
    // period does not show a stale countdown (codex review M1).
    cachedNow = Date.now();
    tickIntervalId = setInterval(() => {
      cachedNow = Date.now();
      tickSubscribers.forEach((cb) => cb());
    }, COUNTDOWN_TICK_MS);
  }
  return () => {
    tickSubscribers.delete(callback);
    if (tickSubscribers.size === 0 && tickIntervalId !== null) {
      clearInterval(tickIntervalId);
      tickIntervalId = null;
    }
  };
}

function getNowSnapshot(): number {
  return cachedNow;
}

interface RateLimitBannerProps {
  readonly snapshot: RateLimitSnapshot | null;
}

/** Outer gate: decides visibility. Returning null before the inner component
 * mounts means idle sessions never subscribe to the tick source. */
export function RateLimitBanner({ snapshot }: RateLimitBannerProps) {
  const level = rateLimitLevel(snapshot);
  if (level === null || !snapshot) return null;
  return <RateLimitBannerContent snapshot={snapshot} level={level} />;
}

interface RateLimitBannerContentProps {
  readonly snapshot: RateLimitSnapshot;
  readonly level: "near" | "reached";
}

function RateLimitBannerContent({
  snapshot,
  level,
}: RateLimitBannerContentProps) {
  const nowMs = useSyncExternalStore(
    subscribeTick,
    getNowSnapshot,
    getNowSnapshot,
  );

  const reachedType = snapshot.rate_limit_reached_type;
  const isReached = level === "reached";
  const title = isReached ? "已触发速率限制" : "接近速率限制";
  const reachedLabel =
    reachedType !== null
      ? (REACHED_TYPE_LABEL[reachedType] ?? reachedType)
      : null;
  const nearHintUsed =
    snapshot.primary?.usedPercent ?? snapshot.secondary?.usedPercent ?? null;

  // role=status + aria-live only on the stable head (title + reason). The
  // countdown ticks every minute; nesting it inside the live region would
  // make screen readers re-announce the whole banner each tick. ``reached``
  // uses status (polite), not alert (assertive) — a rate limit is a heads-up,
  // not an "act now" emergency like a turn failure (codex review M2).
  return (
    <div
      className={`cc-rate-banner${isReached ? " cc-rate-banner--reached" : ""}`}
      role="status"
    >
      <div className="cc-rate-banner__head" aria-live="polite">
        <b className="cc-rate-banner__title">{title}</b>
        {isReached && reachedLabel && (
          <span className="cc-rate-banner__reached-type">{reachedLabel}</span>
        )}
        {!isReached && nearHintUsed !== null && (
          <span className="cc-rate-banner__hint">窗口 {nearHintUsed}%</span>
        )}
      </div>
      <div className="cc-rate-banner__windows">
        {snapshot.primary && (
          <RateWindowView window={snapshot.primary} label="primary" nowMs={nowMs} />
        )}
        {snapshot.secondary && (
          <RateWindowView window={snapshot.secondary} label="secondary" nowMs={nowMs} />
        )}
      </div>
    </div>
  );
}

interface RateWindowViewProps {
  readonly window: RateLimitWindow;
  readonly label: string;
  readonly nowMs: number;
}

function RateWindowView({ window: win, label, nowMs }: RateWindowViewProps) {
  const used = typeof win.usedPercent === "number" ? win.usedPercent : null;
  const high = used !== null && used >= NEAR_THRESHOLD_PERCENT;
  const countdown =
    typeof win.resetsAt === "number"
      ? formatResetCountdown(win.resetsAt, nowMs)
      : null;
  return (
    <div className="cc-rate-window">
      <div className="cc-rate-window__label">{label}</div>
      <div
        className={`cc-rate-window__bar${high ? " cc-rate-window__bar--high" : ""}`}
      >
        {used !== null && (
          <span style={{ width: `${Math.min(100, Math.max(0, used))}%` }} />
        )}
      </div>
      <div className="cc-rate-window__meta">
        {used !== null ? `used ${used}%` : "used —"}
        {countdown && ` · resets ${countdown}`}
      </div>
    </div>
  );
}
