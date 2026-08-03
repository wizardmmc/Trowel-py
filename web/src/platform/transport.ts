/** 把 browser 相对 API 和 desktop 私有 sidecar 统一为同一请求入口。 */

import type { DesktopTransportConfig } from "../../shared/desktop-contracts";
import type {
  TelemetryOperation,
  TelemetrySpan,
} from "../../shared/telemetry-contracts";

interface TransportTelemetry {
  readonly recordSpan: (span: TelemetrySpan) => boolean | void;
}

interface ActiveTransportConfig {
  readonly baseUrl: string;
  readonly credential: string | null;
}

const BROWSER_TRANSPORT: ActiveTransportConfig = {
  baseUrl: "",
  credential: null,
};

let activeTransport: ActiveTransportConfig = BROWSER_TRANSPORT;
let transportTelemetry: TransportTelemetry | null = null;
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

/** 把当前未完成请求数发布给桌面端烟测等待器。 */
function publishPendingRequests(): void {
  if (typeof window !== "undefined") {
    window.__TROWEL_PENDING_TRANSPORT_REQUESTS__ = pendingRequests;
  }
}

/** 切换到指定 loopback sidecar，并保存后续请求使用的桌面凭据。 */
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

/** 绑定或移除 renderer span 的记录出口。 */
export function configureTransportTelemetry(
  telemetry: TransportTelemetry | null,
): void {
  transportTelemetry = telemetry;
}

/** 把相对 API 地址解析为 browser 原地址或 desktop sidecar 绝对地址。 */
export function resolveTransportUrl(url: string): string {
  if (!activeTransport.baseUrl || !url.startsWith("/")) return url;
  return new URL(url, `${activeTransport.baseUrl}/`).toString();
}

/** 通过当前 transport 发请求，并只给 Trowel 自有 API 注入认证和 trace。 */
export function transportFetch(
  url: string,
  options?: RequestInit,
): Promise<Response> {
  const resolvedUrl = resolveTransportUrl(url);
  if (!targetsOwnedApi(url, resolvedUrl)) {
    return trackedFetch(resolvedUrl, options);
  }
  if (!activeTransport.credential) {
    return tracedFetch(resolvedUrl, options);
  }
  const headers = new Headers(options?.headers);
  headers.set("Authorization", `Bearer ${activeTransport.credential}`);
  return tracedFetch(resolvedUrl, { ...options, headers });
}

/** 清除跨测试保留的 transport、遥测出口和请求计数。 */
export function resetTransportForTests(): void {
  activeTransport = BROWSER_TRANSPORT;
  transportTelemetry = null;
  pendingRequests = 0;
  publishPendingRequests();
}

/** 为命中的受控 API 创建 renderer client span 并传播 traceparent。 */
function tracedFetch(url: string, options?: RequestInit): Promise<Response> {
  const operation = observedOperation(url);
  if (operation === null || transportTelemetry === null) {
    return trackedFetch(url, options);
  }
  const traceId = crypto.randomUUID().replaceAll("-", "");
  const spanId = crypto.randomUUID().replaceAll("-", "").slice(0, 16);
  const headers = new Headers(options?.headers);
  if (headers.has("traceparent")) {
    return trackedFetch(url, options);
  }
  headers.set("traceparent", `00-${traceId}-${spanId}-01`);
  const startedAt = new Date();
  try {
    return trackedFetch(url, { ...options, headers }).then(
      (response) => {
        recordTransportSpan(
          traceId,
          spanId,
          operation,
          startedAt,
          new Date(),
          response.status >= 500 ? "error" : "ok",
        );
        return response;
      },
      (error: unknown) => {
        recordTransportSpan(
          traceId,
          spanId,
          operation,
          startedAt,
          new Date(),
          "error",
        );
        throw error;
      },
    );
  } catch (error) {
    recordTransportSpan(
      traceId,
      spanId,
      operation,
      startedAt,
      new Date(),
      "error",
    );
    throw error;
  }
}

/** 把固定 API 组映射到不含动态路径的受控 operation。 */
function observedOperation(url: string): TelemetryOperation | null {
  const path = new URL(url, "http://trowel.local").pathname;
  if (path.startsWith("/api/statistics/")) return "http.statistics.query";
  if (
    path.startsWith("/api/agent/sessions/") &&
    ["/events", "/messages", "/turns"].some((suffix) => path.endsWith(suffix))
  ) {
    return "http.agent.messages";
  }
  return null;
}

/** 判断目标是否为 browser 同源 API 或当前 desktop 私有 sidecar。 */
function targetsOwnedApi(originalUrl: string, resolvedUrl: string): boolean {
  if (activeTransport.baseUrl) {
    return new URL(resolvedUrl).origin === activeTransport.baseUrl;
  }
  if (originalUrl.startsWith("/") && !originalUrl.startsWith("//")) return true;
  if (typeof window === "undefined") return false;
  return (
    new URL(resolvedUrl, window.location.href).origin === window.location.origin
  );
}

/** 记录一个不含 URL、正文或会话身份的 renderer HTTP span。 */
function recordTransportSpan(
  traceId: string,
  spanId: string,
  operation: TelemetryOperation,
  startedAt: Date,
  endedAt: Date,
  status: "ok" | "error",
): void {
  try {
    transportTelemetry?.recordSpan({
      trace_id: traceId,
      span_id: spanId,
      parent_span_id: null,
      started_at: startedAt.toISOString(),
      ended_at: endedAt.toISOString(),
      component: "renderer",
      operation,
      status,
      runtime: null,
      model: null,
      session_ref: null,
      call_ref: null,
      attributes: { quality: "reliable", transport: "http" },
      links: [],
    });
  } catch {
    // 观测失败不能改变原请求的成功或失败语义。
  }
}

declare global {
  interface Window {
    __TROWEL_PENDING_TRANSPORT_REQUESTS__?: number;
  }
}
