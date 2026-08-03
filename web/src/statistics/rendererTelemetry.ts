/** 记录 renderer 自身从导航开始到 React 首次绘制的耗时。 */

import type { TelemetryBatcher } from "../../shared/telemetry-batcher";

export function recordRendererReady(
  batcher: TelemetryBatcher,
  navigationStartedAtMs: number,
  renderedAt: Date,
): void {
  /** renderer.measure 只覆盖当前 renderer，不冒充整个桌面冷启动耗时。 */
  const traceId = crypto.randomUUID().replaceAll("-", "");
  batcher.recordSpan({
    trace_id: traceId,
    span_id: crypto.randomUUID().replaceAll("-", "").slice(0, 16),
    parent_span_id: null,
    started_at: new Date(navigationStartedAtMs).toISOString(),
    ended_at: renderedAt.toISOString(),
    component: "renderer",
    operation: "renderer.measure",
    status: "ok",
    runtime: null,
    model: null,
    session_ref: null,
    call_ref: null,
    attributes: { quality: "reliable", sampled: false },
    links: [],
  });
}
