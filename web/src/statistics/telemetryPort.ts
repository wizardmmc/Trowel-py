/** 用统一 renderer transport 提交遥测批次并创建共享 batcher。 */

import {
  TelemetryBatcher,
  type TelemetryBatcherOptions,
} from "../../shared/telemetry-batcher";
import type {
  TelemetryBatch,
  TelemetryComponent,
  TelemetrySubmitData,
} from "../../shared/telemetry-contracts";
import { transportFetch } from "../platform/transport";
import type { ApiEnvelope } from "./domain/types";

export async function sendRendererTelemetryBatch(
  batch: TelemetryBatch,
): Promise<TelemetrySubmitData> {
  const response = await transportFetch("/api/telemetry/batches", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(batch),
  });
  const envelope = (await response.json()) as ApiEnvelope<TelemetrySubmitData>;
  if (!response.ok || !envelope.success || envelope.data === null) {
    throw new Error(envelope.error ?? `Telemetry request failed: ${response.status}`);
  }
  return envelope.data;
}

export function createRendererTelemetryPort(
  sourceComponent: TelemetryComponent = "renderer",
  options: Omit<TelemetryBatcherOptions, "sourceComponent" | "send"> = {},
): TelemetryBatcher {
  return new TelemetryBatcher({
    ...options,
    sourceComponent,
    send: sendRendererTelemetryBatch,
  });
}
