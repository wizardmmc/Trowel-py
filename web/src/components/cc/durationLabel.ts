/**
 * Format a turn's duration (whole seconds) as cc-tui-style "Ran for …" text.
 *
 *   < 60s   → "Ns"      (45 → "45s")
 *   < 3600s → "Mm Ss"   (78 → "1m 18s", 211 → "3m 31s")
 *   ≥ 3600s → "Hh Mm"   (3900 → "1h 5m")
 *
 * Used by the per-turn "Ran for …" label (MessageList). Callers gate on
 * seconds > 0; this function handles any non-negative integer itself.
 */
export function formatRunDuration(seconds: number): string {
  if (seconds < 60) {
    return `${seconds}s`;
  }
  if (seconds < 3600) {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}m ${s}s`;
  }
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}
