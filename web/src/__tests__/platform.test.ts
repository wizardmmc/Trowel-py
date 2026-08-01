/** 验证 renderer 根据 preload 是否存在选择 browser 或 desktop 平台实现。 */

import { afterEach, expect, it, vi } from "vitest";
import type { DesktopBridge } from "../../shared/desktop-contracts";
import {
  getPlatform,
  initializePlatform,
  resetPlatformForTests,
} from "../platform";

afterEach(() => {
  resetPlatformForTests();
  delete (window as Window & { trowelDesktop?: DesktopBridge }).trowelDesktop;
  vi.unstubAllGlobals();
});

it("uses browser capabilities when no preload bridge exists", async () => {
  await initializePlatform();

  expect(getPlatform().environment).toBe("browser");
  expect(await getPlatform().selectWorkdir("/repo")).toBeNull();
});

it("loads desktop context and verifies renderer-to-sidecar transport", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ success: true, data: { status: "ok" } }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  const bridge = {
    getContext: vi.fn().mockResolvedValue({
      environment: "desktop",
      appVersion: "0.1.0",
      instanceId: "instance-123",
      transport: {
        baseUrl: "http://127.0.0.1:43123",
        credential: "desktop-secret",
      },
    }),
    selectWorkdir: vi.fn().mockResolvedValue("/repo"),
    openExternal: vi.fn(),
    openPath: vi.fn(),
    requestQuit: vi.fn(),
    getDiagnostics: vi.fn(),
    retrySidecar: vi.fn(),
    openLogs: vi.fn(),
  } satisfies DesktopBridge;
  (window as Window & { trowelDesktop?: DesktopBridge }).trowelDesktop = bridge;

  await initializePlatform();

  expect(getPlatform().environment).toBe("desktop");
  expect(await getPlatform().selectWorkdir("/old")).toBe("/repo");
  const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(url).toBe("http://127.0.0.1:43123/api/health");
  expect(new Headers(options.headers).get("Authorization")).toBe(
    "Bearer desktop-secret",
  );
});
