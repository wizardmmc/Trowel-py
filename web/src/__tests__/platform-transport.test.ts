/** 验证 browser 与 desktop 共用同一套 HTTP transport 入口。 */

import { afterEach, expect, it, vi } from "vitest";
import {
  configureTransport,
  configureTransportTelemetry,
  resetTransportForTests,
  resolveTransportUrl,
  transportFetch,
} from "../platform/transport";

afterEach(() => {
  resetTransportForTests();
  vi.unstubAllGlobals();
});

it("propagates a renderer span to observed Agent HTTP requests", async () => {
  const recordSpan = vi.fn();
  configureTransportTelemetry({ recordSpan });
  const fetchMock = vi
    .fn()
    .mockResolvedValue(new Response(null, { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  await transportFetch("/api/agent/sessions/session-private/turns", {
    method: "POST",
  });

  const [, options] = fetchMock.mock.calls[0] as [string, RequestInit];
  const traceparent = new Headers(options.headers).get("traceparent");
  expect(traceparent).toMatch(/^00-[0-9a-f]{32}-[0-9a-f]{16}-01$/);
  expect(recordSpan).toHaveBeenCalledTimes(1);
  const span = recordSpan.mock.calls[0]?.[0];
  expect(traceparent).toBe(`00-${span.trace_id}-${span.span_id}-01`);
  expect(span.operation).toBe("http.agent.messages");
  expect(JSON.stringify(span)).not.toContain("session-private");
});

it("keeps relative API URLs unchanged in browser mode", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response());
  vi.stubGlobal("fetch", fetchMock);

  await transportFetch("/api/health");

  expect(fetchMock).toHaveBeenCalledWith("/api/health", undefined);
  expect(resolveTransportUrl("https://example.com/path")).toBe(
    "https://example.com/path",
  );
});

it("targets the private sidecar and adds its credential in desktop mode", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response());
  vi.stubGlobal("fetch", fetchMock);
  configureTransport({
    baseUrl: "http://127.0.0.1:43123/",
    credential: "desktop-secret",
  });

  await transportFetch("/api/health", {
    headers: { "Content-Type": "application/json" },
  });

  const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
  const headers = options.headers as Headers;
  expect(url).toBe("http://127.0.0.1:43123/api/health");
  expect(headers.get("Authorization")).toBe("Bearer desktop-secret");
  expect(headers.get("Content-Type")).toBe("application/json");
});

it("never sends the sidecar credential to an external origin", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response());
  vi.stubGlobal("fetch", fetchMock);
  configureTransport({
    baseUrl: "http://127.0.0.1:43123",
    credential: "desktop-secret",
  });

  await transportFetch("https://example.com/public.json");

  const [, options] = fetchMock.mock.calls[0] as [
    string,
    RequestInit | undefined,
  ];
  expect(new Headers(options?.headers).has("Authorization")).toBe(false);
});

it("never propagates a local trace to an external origin", async () => {
  const recordSpan = vi.fn();
  configureTransportTelemetry({ recordSpan });
  const fetchMock = vi.fn().mockResolvedValue(new Response());
  vi.stubGlobal("fetch", fetchMock);

  await transportFetch("https://example.com/api/statistics/public");

  const [, options] = fetchMock.mock.calls[0] as [
    string,
    RequestInit | undefined,
  ];
  expect(new Headers(options?.headers).has("traceparent")).toBe(false);
  expect(recordSpan).not.toHaveBeenCalled();
});

it("treats a protocol-relative URL as external instead of a local API", async () => {
  const recordSpan = vi.fn();
  configureTransportTelemetry({ recordSpan });
  const fetchMock = vi.fn().mockResolvedValue(new Response());
  vi.stubGlobal("fetch", fetchMock);

  await transportFetch("//example.com/api/statistics/public");

  const [, options] = fetchMock.mock.calls[0] as [
    string,
    RequestInit | undefined,
  ];
  expect(new Headers(options?.headers).has("traceparent")).toBe(false);
  expect(recordSpan).not.toHaveBeenCalled();
});

it("does not turn a successful API request into a failure when telemetry throws", async () => {
  configureTransportTelemetry({
    recordSpan: () => {
      throw new Error("telemetry unavailable");
    },
  });
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response(null, { status: 200 })),
  );

  await expect(
    transportFetch("/api/statistics/runtime"),
  ).resolves.toBeInstanceOf(Response);
});

it("publishes pending request count for deterministic desktop smoke", async () => {
  let finishRequest!: (response: Response) => void;
  const pendingResponse = new Promise<Response>((resolve) => {
    finishRequest = resolve;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(() => pendingResponse),
  );

  const request = transportFetch("/api/health");
  expect(window.__TROWEL_PENDING_TRANSPORT_REQUESTS__).toBe(1);

  finishRequest(new Response());
  await request;

  expect(window.__TROWEL_PENDING_TRANSPORT_REQUESTS__).toBe(0);
});
