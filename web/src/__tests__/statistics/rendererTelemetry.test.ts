/** 验证 renderer 首绘埋点使用独立 operation 且不携带页面正文。 */

import { expect, it, vi } from "vitest";
import { TelemetryBatcher } from "../../../shared/telemetry-batcher";
import type { TelemetryBatch } from "../../../shared/telemetry-contracts";
import { recordRendererReady } from "../../statistics/rendererTelemetry";

it("records renderer navigation to first paint", async () => {
  const batches: TelemetryBatch[] = [];
  const batcher = new TelemetryBatcher({
    sourceComponent: "renderer",
    send: vi.fn(async (batch) => {
      batches.push(batch);
      return {
        accepted: batch.spans.length,
        rejected: 0,
        dropped: 0,
        duplicate: false,
        error_categories: {},
      };
    }),
    flushIntervalMs: 10_000,
  });

  recordRendererReady(
    batcher,
    Date.parse("2026-08-03T01:00:00.000Z"),
    new Date("2026-08-03T01:00:00.250Z"),
  );
  await batcher.drain(100);

  expect(batches[0].spans[0]).toMatchObject({
    component: "renderer",
    operation: "renderer.measure",
    status: "ok",
  });
  expect(batches[0].spans[0].session_ref).toBeNull();
});
