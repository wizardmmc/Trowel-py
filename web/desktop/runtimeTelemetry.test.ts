/** 验证 Electron 运行埋点区分窗口关闭、应用退出与首屏幂等。 */

// @vitest-environment node

import { expect, it, vi } from "vitest";
import { TelemetryBatcher } from "../shared/telemetry-batcher";
import type { TelemetryBatch } from "../shared/telemetry-contracts";
import { DesktopRuntimeTelemetry } from "./runtimeTelemetry";

it("records stable startup and crash operations without dynamic attributes", async () => {
  const sent: TelemetryBatch[] = [];
  const batcher = new TelemetryBatcher({
    sourceComponent: "electron",
    send: vi.fn(async (batch) => {
      sent.push(batch);
      return {
        accepted: batch.spans.length + batch.metrics.length,
        rejected: 0,
        dropped: 0,
        duplicate: false,
        error_categories: {},
      };
    }),
    batchSize: 20,
    flushIntervalMs: 10_000,
  });
  const telemetry = new DesktopRuntimeTelemetry(
    batcher,
    new Date("2026-08-03T01:00:00.000Z"),
  );

  telemetry.recordSidecarReady(new Date("2026-08-03T01:00:01.000Z"));
  telemetry.recordFirstScreen(new Date("2026-08-03T01:00:02.000Z"));
  telemetry.recordFirstScreen(new Date("2026-08-03T01:00:03.000Z"));
  telemetry.recordWindowClose(new Date("2026-08-03T01:00:04.000Z"));
  telemetry.recordRendererCrash(new Date("2026-08-03T01:00:05.000Z"));
  telemetry.recordSidecarRestart(new Date("2026-08-03T01:00:06.000Z"));
  await telemetry.drain(100);

  const [batch] = sent;
  expect(batch.spans.map((span) => span.operation)).toEqual([
    "desktop.start.sidecar_ready",
    "desktop.start.first_screen",
    "desktop.window.close",
    "desktop.renderer.crash",
  ]);
  expect(batch.metrics[0].name).toBe("sidecar.restart");
  expect(JSON.stringify(batch)).not.toContain("session_id");
});
