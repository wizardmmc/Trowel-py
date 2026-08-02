/** 在 renderer 启动时选择平台实现，并在挂载产品 UI 前验证 sidecar。 */

import type { DesktopBridge } from "../../shared/desktop-contracts";
import { browserPlatform } from "./browser";
import type { PlatformPort } from "./contracts";
import { createDesktopPlatform } from "./desktop";
import {
  configureTransport,
  resetTransportForTests,
  transportFetch,
} from "./transport";

let activePlatform: PlatformPort = browserPlatform;

export async function initializePlatform(): Promise<void> {
  const bridge = window.trowelDesktop;
  if (!bridge) {
    activePlatform = browserPlatform;
    document.documentElement.dataset.platform = "browser";
    return;
  }
  const context = await bridge.getContext();
  configureTransport(context.transport);
  const response = await transportFetch("/api/health");
  if (!response.ok) {
    throw new Error(`desktop sidecar health check failed: ${response.status}`);
  }
  const envelope = (await response.json()) as { readonly success?: boolean };
  if (!envelope.success) {
    throw new Error("desktop sidecar health check returned an invalid envelope");
  }
  activePlatform = createDesktopPlatform(bridge, context);
  document.documentElement.dataset.platform = "desktop";
}

export function getPlatform(): PlatformPort {
  return activePlatform;
}

export function resetPlatformForTests(): void {
  activePlatform = browserPlatform;
  delete document.documentElement.dataset.platform;
  resetTransportForTests();
  window.__TROWEL_RENDERER_READY__ = false;
}

declare global {
  interface Window {
    trowelDesktop?: DesktopBridge;
    __TROWEL_RENDERER_READY__?: boolean;
  }
}
