import { useSyncExternalStore } from "react";
import type { RateLimitSnapshot, RateLimitWindow } from "../../api/ccTypes";
import {
  NEAR_THRESHOLD_PERCENT,
  REACHED_TYPE_LABEL,
  formatResetCountdown,
  rateLimitLevel,
} from "./rateLimit";

/** 倒计时只需分钟精度，避免无意义的高频重渲染。 */
const COUNTDOWN_TICK_MS = 60_000;

/** 所有可见 banner 共用一个引用计数计时器。 */
let cachedNow = Date.now();
const tickSubscribers = new Set<() => void>();
let tickIntervalId: ReturnType<typeof setInterval> | null = null;

function subscribeTick(callback: () => void): () => void {
  tickSubscribers.add(callback);
  if (tickIntervalId === null) {
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

export function RateLimitBanner({ snapshot }: RateLimitBannerProps) {
  const level = rateLimitLevel(snapshot);
  // 不可见时不挂载订阅，空闲会话不会启动计时器。
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

  // aria-live 只包稳定标题，避免每分钟重复朗读倒计时。
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
