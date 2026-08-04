/** 用桌面实例凭据把 Electron Host 遥测提交给私有 sidecar。 */

import type { DesktopTransportConfig } from "../shared/desktop-contracts";
import type {
  TelemetryBatch,
  TelemetrySubmitData,
} from "../shared/telemetry-contracts";

interface TelemetryEnvelope {
  readonly success: boolean;
  readonly data: TelemetrySubmitData | null;
  readonly error: string | null;
}

type Fetcher = (input: string, init?: RequestInit) => Promise<Response>;

export function createDesktopTelemetrySender(
  config: DesktopTransportConfig,
  fetcher: Fetcher = fetch,
): (batch: TelemetryBatch) => Promise<TelemetrySubmitData> {
  const endpoint = new URL("/api/telemetry/batches", normalizedBaseUrl(config));
  return async (batch) => {
    const response = await fetcher(endpoint.toString(), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${config.credential}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(batch),
    });
    const envelope = (await response.json()) as TelemetryEnvelope;
    if (!response.ok || !envelope.success || envelope.data === null) {
      throw new Error(envelope.error ?? `Telemetry request failed: ${response.status}`);
    }
    return envelope.data;
  };
}

function normalizedBaseUrl(config: DesktopTransportConfig): string {
  const endpoint = new URL(config.baseUrl);
  if (
    endpoint.protocol !== "http:" ||
    !["127.0.0.1", "localhost"].includes(endpoint.hostname) ||
    !config.credential
  ) {
    throw new Error("desktop telemetry requires an authenticated loopback sidecar");
  }
  return `${endpoint.origin}/`;
}
