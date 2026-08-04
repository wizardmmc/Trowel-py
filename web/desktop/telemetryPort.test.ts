/** 验证 Electron Host 用实例凭据提交遥测且隔离断连。 */

import { expect, it, vi } from "vitest";
import { createDesktopTelemetrySender } from "./telemetryPort";

const batch = {
  batch_id: "batch-desktop-1",
  schema_version: 1 as const,
  source_component: "electron" as const,
  collected_at: "2026-08-03T12:00:00.000Z",
  mode: "normal" as const,
  spans: [],
  metrics: [],
};

it("posts to the private sidecar with its bearer credential", async () => {
  const fetcher = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        success: true,
        data: {
          accepted: 0,
          rejected: 0,
          dropped: 0,
          duplicate: false,
          error_categories: {},
        },
        error: null,
      }),
      { status: 202 },
    ),
  );
  const send = createDesktopTelemetrySender(
    { baseUrl: "http://127.0.0.1:43123", credential: "desktop-secret" },
    fetcher,
  );

  const result = await send(batch);

  const [url, options] = fetcher.mock.calls[0] as [string, RequestInit];
  expect(url).toBe("http://127.0.0.1:43123/api/telemetry/batches");
  expect(new Headers(options.headers).get("Authorization")).toBe(
    "Bearer desktop-secret",
  );
  expect(result.accepted).toBe(0);
});

it("rejects a failed sidecar envelope so the batcher can retry", async () => {
  const send = createDesktopTelemetrySender(
    { baseUrl: "http://127.0.0.1:43123", credential: "desktop-secret" },
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ success: false, data: null, error: "unavailable" }),
        { status: 503 },
      ),
    ),
  );

  await expect(send(batch)).rejects.toThrow("unavailable");
});
