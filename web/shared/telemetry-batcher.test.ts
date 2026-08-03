/** 验证 Electron 与 renderer 共用的有界遥测批处理器。 */

import { expect, it, vi } from "vitest";
import { TelemetryBatcher } from "./telemetry-batcher";
import type { TelemetrySpan, TelemetrySubmitData } from "./telemetry-contracts";

const accepted = {
  accepted: 1,
  rejected: 0,
  dropped: 0,
  duplicate: false,
  error_categories: {},
};

function span(index: number): TelemetrySpan {
  return {
    trace_id: index.toString(16).padStart(32, "0"),
    span_id: index.toString(16).padStart(16, "0"),
    parent_span_id: null,
    started_at: "2026-08-03T12:00:00.000Z",
    ended_at: "2026-08-03T12:00:00.010Z",
    component: "renderer",
    operation: "renderer.measure",
    status: "ok",
    runtime: null,
    model: null,
    session_ref: null,
    call_ref: null,
    attributes: { quality: "reliable", sampled: true },
    links: [],
  };
}

function runtimeSpan(index: number): TelemetrySpan {
  return {
    ...span(index),
    component: "runtime",
    operation: "runtime.call",
    runtime: "codex",
    model: "future-model-native",
  };
}

it("preserves model identifiers reported by the runtime", async () => {
  const send = vi.fn().mockResolvedValue(accepted);
  const batcher = new TelemetryBatcher({
    sourceComponent: "renderer",
    send,
    flushIntervalMs: 60_000,
    idFactory: () => "batch-runtime-model",
  });

  batcher.recordSpan(runtimeSpan(1));
  await batcher.drain(1000);

  expect(send.mock.calls[0]?.[0].spans[0]?.model).toBe("future-model-native");
});

it("flushes no more than the configured batch size", async () => {
  const send = vi.fn().mockResolvedValue(accepted);
  const batcher = new TelemetryBatcher({
    sourceComponent: "renderer",
    send,
    batchSize: 2,
    flushIntervalMs: 60_000,
    idFactory: (() => {
      let value = 0;
      return () => `batch-test-${++value}`;
    })(),
  });

  batcher.recordSpan(span(1));
  batcher.recordSpan(span(2));
  batcher.recordSpan(span(3));
  await batcher.drain(1000);

  expect(send).toHaveBeenCalledTimes(2);
  expect(send.mock.calls[0]?.[0].spans).toHaveLength(2);
  expect(send.mock.calls[1]?.[0].spans).toHaveLength(1);
});

it("drops at capacity without blocking the producer", () => {
  const batcher = new TelemetryBatcher({
    sourceComponent: "renderer",
    send: vi.fn().mockResolvedValue(accepted),
    queueCapacity: 1,
    flushIntervalMs: 60_000,
    idFactory: () => "batch-capacity",
  });

  expect(batcher.recordSpan(span(1))).toBe(true);
  expect(batcher.recordSpan(span(2))).toBe(false);
  expect(batcher.snapshot().dropped).toBe(1);
});

it("retries a disconnect finitely and drains successfully", async () => {
  const send = vi
    .fn()
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(accepted);
  const batcher = new TelemetryBatcher({
    sourceComponent: "renderer",
    send,
    maxRetries: 1,
    retryBaseMs: 1,
    flushIntervalMs: 60_000,
    idFactory: () => "batch-retry",
  });
  batcher.recordSpan(span(1));

  const report = await batcher.drain(1000);

  expect(report.drained).toBe(true);
  expect(send).toHaveBeenCalledTimes(2);
  expect(report.dropped).toBe(0);
});

it("counts records rejected by an accepted backend batch", async () => {
  const batcher = new TelemetryBatcher({
    sourceComponent: "renderer",
    send: vi.fn().mockResolvedValue({
      accepted: 1,
      rejected: 1,
      dropped: 0,
      duplicate: false,
      error_categories: { invalid_schema: 1 },
    }),
    flushIntervalMs: 60_000,
    idFactory: () => "batch-partial",
  });
  batcher.recordSpan(span(1));
  batcher.recordSpan(span(2));

  const report = await batcher.drain(1000);

  expect(report.drained).toBe(true);
  expect(report.dropped).toBe(1);
});

it("returns on drain timeout and counts remaining records", async () => {
  const send = vi.fn(() => new Promise<TelemetrySubmitData>(() => undefined));
  const batcher = new TelemetryBatcher({
    sourceComponent: "renderer",
    send,
    flushIntervalMs: 60_000,
    idFactory: () => "batch-timeout",
  });
  batcher.recordSpan(span(1));

  const report = await batcher.drain(5);

  expect(report.drained).toBe(false);
  expect(report.dropped).toBe(1);
});
