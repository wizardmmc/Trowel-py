/** 验证 statistics API 在 browser 与 desktop 下复用统一 transport。 */

import { afterEach, expect, it, vi } from "vitest";
import {
  configureTransport,
  resetTransportForTests,
} from "../../platform/transport";
import {
  fetchAgentStatistics,
  fetchCallDetail,
  fetchCallStatistics,
  fetchMemoryStatistics,
  fetchRuntimeStatistics,
  fetchTelemetryStatistics,
} from "../../statistics/transport/api";
import { runtimeStatisticsFixture } from "./runtimeStatisticsFixture";
import {
  callDetailFixture,
  callListFixture,
} from "./callStatisticsFixture";

afterEach(() => {
  resetTransportForTests();
  vi.unstubAllGlobals();
});

const responseData = {
  generated_at: "2026-08-03T12:00:00Z",
  window_start: "2026-08-03T00:00:00Z",
  window_end: "2026-08-04T00:00:00Z",
  timezone: "UTC",
  sample_size: 0,
  quality: "unavailable",
  freshness: {
    telemetry: { updated_at: null, status: "unavailable" },
  },
  resolution: "hour",
  collector: {
    accepted: 0,
    rejected: 0,
    dropped: 0,
    queued_records: 0,
    inflight_records: 0,
    running: true,
    last_error_category: null,
  },
  database_bytes: { database: 4096, wal: 0, shm: 0, total: 4096 },
  histogram_upper_bounds_ms: [1, 5, null],
  spans: [],
  metrics: [],
};

it("uses a relative statistics URL in browser mode", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({ success: true, data: responseData, error: null }),
      { status: 200 },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);

  const result = await fetchTelemetryStatistics(
    { startDate: "2026-08-03", endDate: "2026-08-03", timezone: "UTC" },
    "hour",
  );

  expect(result.sample_size).toBe(0);
  expect(fetchMock.mock.calls[0]?.[0]).toBe(
    "/api/statistics/telemetry?start_date=2026-08-03&end_date=2026-08-03&timezone=UTC&resolution=hour",
  );
});

it("uses the same API with desktop base URL and credential", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({ success: true, data: responseData, error: null }),
      { status: 200 },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
  configureTransport({
    baseUrl: "http://127.0.0.1:43123",
    credential: "desktop-secret",
  });

  await fetchTelemetryStatistics(
    { startDate: "2026-08-03", endDate: "2026-08-03", timezone: "UTC" },
    "day",
  );

  const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(url).toContain("http://127.0.0.1:43123/api/statistics/telemetry?");
  expect(new Headers(options.headers).get("Authorization")).toBe(
    "Bearer desktop-secret",
  );
});

it("throws the envelope error instead of treating missing data as zero", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ success: false, data: null, error: "source unavailable" }),
        { status: 503 },
      ),
    ),
  );

  await expect(
    fetchTelemetryStatistics(
      { startDate: "2026-08-03", endDate: "2026-08-03", timezone: "UTC" },
      "hour",
    ),
  ).rejects.toThrow("source unavailable");
});

it("requests Agent statistics with the shared date range", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        success: true,
        data: { quality: "unavailable", sample_size: 0 },
        error: null,
      }),
      { status: 200 },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);

  const result = await fetchAgentStatistics({
    startDate: "2026-08-03",
    endDate: "2026-08-03",
    timezone: "Asia/Shanghai",
  });

  expect(result.sample_size).toBe(0);
  expect(fetchMock.mock.calls[0]?.[0]).toBe(
    "/api/statistics/agent?start_date=2026-08-03&end_date=2026-08-03&timezone=Asia%2FShanghai",
  );
});

it("requests Memory statistics with the shared date range", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        success: true,
        data: { quality: "partial", sample_size: 12 },
        error: null,
      }),
      { status: 200 },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);

  const result = await fetchMemoryStatistics({
    startDate: "2026-08-02",
    endDate: "2026-08-03",
    timezone: "Asia/Shanghai",
  });

  expect(result.sample_size).toBe(12);
  expect(fetchMock.mock.calls[0]?.[0]).toBe(
    "/api/statistics/memory?start_date=2026-08-02&end_date=2026-08-03&timezone=Asia%2FShanghai",
  );
});

it("requests Runtime statistics with the shared date range", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        success: true,
        data: runtimeStatisticsFixture,
        error: null,
      }),
      { status: 200 },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);

  const result = await fetchRuntimeStatistics({
    startDate: "2026-08-03",
    endDate: "2026-08-03",
    timezone: "Asia/Shanghai",
  });

  expect(result.sidecar.rss.value).toBe(1_084_227_584);
  expect(fetchMock.mock.calls[0]?.[0]).toBe(
    "/api/statistics/runtime?start_date=2026-08-03&end_date=2026-08-03&timezone=Asia%2FShanghai",
  );
});

it("adds only active call filters and a stable paging cursor", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({ success: true, data: callListFixture, error: null }),
      { status: 200 },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);

  await fetchCallStatistics(
    { startDate: "2026-08-03", endDate: "2026-08-03", timezone: "UTC" },
    {
      component: "mcp",
      operation: "all",
      runtime: "codex",
      status: "ok",
      minimumDurationMs: 1000,
    },
    "next-page",
  );

  expect(fetchMock.mock.calls[0]?.[0]).toBe(
    "/api/statistics/calls?start_date=2026-08-03&end_date=2026-08-03&timezone=UTC&component=mcp&runtime=codex&status=ok&minimum_duration_ms=1000&cursor=next-page",
  );
});

it("encodes the trace identity when requesting call detail", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({ success: true, data: callDetailFixture, error: null }),
      { status: 200 },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);

  await fetchCallDetail("trace/id");

  expect(fetchMock.mock.calls[0]?.[0]).toBe(
    "/api/statistics/calls/trace%2Fid",
  );
});
