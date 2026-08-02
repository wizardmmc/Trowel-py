/** 把 browser 相对 API 和 desktop 私有 sidecar 统一为同一请求入口。 */

import type { DesktopTransportConfig } from "../../shared/desktop-contracts";

interface ActiveTransportConfig {
  readonly baseUrl: string;
  readonly credential: string | null;
}

const BROWSER_TRANSPORT: ActiveTransportConfig = {
  baseUrl: "",
  credential: null,
};

let activeTransport: ActiveTransportConfig = BROWSER_TRANSPORT;
let pendingRequests = 0;

/** 执行请求并向 Desktop smoke 暴露尚未结束的统一 transport 数量。 */
function trackedFetch(url: string, options?: RequestInit): Promise<Response> {
  pendingRequests += 1;
  publishPendingRequests();
  try {
    return fetch(url, options).finally(() => {
      pendingRequests -= 1;
      publishPendingRequests();
    });
  } catch (error) {
    pendingRequests -= 1;
    publishPendingRequests();
    throw error;
  }
}

function publishPendingRequests(): void {
  if (typeof window !== "undefined") {
    window.__TROWEL_PENDING_TRANSPORT_REQUESTS__ = pendingRequests;
  }
}

export function configureTransport(config: DesktopTransportConfig): void {
  const endpoint = new URL(config.baseUrl);
  if (
    endpoint.protocol !== "http:" ||
    !["127.0.0.1", "localhost"].includes(endpoint.hostname)
  ) {
    throw new Error("desktop transport must use a loopback HTTP endpoint");
  }
  if (!config.credential) {
    throw new Error("desktop transport credential is required");
  }
  activeTransport = {
    baseUrl: endpoint.origin,
    credential: config.credential,
  };
}

export function resolveTransportUrl(url: string): string {
  if (!activeTransport.baseUrl || !url.startsWith("/")) return url;
  return new URL(url, `${activeTransport.baseUrl}/`).toString();
}

export function transportFetch(
  url: string,
  options?: RequestInit,
): Promise<Response> {
  const resolvedUrl = resolveTransportUrl(url);
  if (!activeTransport.credential) {
    return trackedFetch(resolvedUrl, options);
  }
  if (new URL(resolvedUrl).origin !== activeTransport.baseUrl) {
    return trackedFetch(resolvedUrl, options);
  }
  const headers = new Headers(options?.headers);
  headers.set("Authorization", `Bearer ${activeTransport.credential}`);
  return trackedFetch(resolvedUrl, { ...options, headers });
}

export function resetTransportForTests(): void {
  activeTransport = BROWSER_TRANSPORT;
  pendingRequests = 0;
  publishPendingRequests();
}

declare global {
  interface Window {
    __TROWEL_PENDING_TRANSPORT_REQUESTS__?: number;
  }
}
