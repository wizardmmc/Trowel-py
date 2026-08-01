/** 把 Vite 的开发态 /api 请求动态转发到当前 Desktop Agent Service。 */

import http, {
  type IncomingHttpHeaders,
  type IncomingMessage,
  type ServerResponse,
} from "node:http";

import { readAgentServiceDescriptor } from "./serviceDescriptor";

export interface AgentServiceProxyOptions {
  readonly descriptorPath?: string;
  readonly fallbackBaseUrl: string;
}

type ConnectNext = () => void;
type AgentServiceProxy = (
  request: IncomingMessage,
  response: ServerResponse,
  next?: ConnectNext,
) => void;

const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "proxy-connection",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

/** 判断请求是否属于后端 API，其他 Vite 资源继续交给后续中间件。 */
function isApiRequest(rawUrl: string): boolean {
  return rawUrl === "/api" || rawUrl.startsWith("/api/") || rawUrl.startsWith("/api?");
}

/** 移除只对当前连接有效的头，以及 Connection 动态声明的附加头。 */
function endToEndHeaders(source: IncomingHttpHeaders): IncomingHttpHeaders {
  const blocked = new Set(HOP_BY_HOP_HEADERS);
  for (const [name, value] of Object.entries(source)) {
    if (name.toLowerCase() !== "connection") continue;
    const declared = Array.isArray(value) ? value.join(",") : (value ?? "");
    for (const token of declared.split(",")) {
      const normalized = token.trim().toLowerCase();
      if (normalized) blocked.add(normalized);
    }
  }
  return Object.fromEntries(
    Object.entries(source).filter(([name]) => !blocked.has(name.toLowerCase())),
  );
}

/** 生成转发头；浏览器永远看不到 descriptor 中的私有凭据。 */
function upstreamHeaders(
  source: IncomingHttpHeaders,
  target: URL,
  credential: string | null,
): IncomingHttpHeaders {
  const headers = { ...endToEndHeaders(source), host: target.host };
  delete headers.authorization;
  if (credential) headers.authorization = `Bearer ${credential}`;
  return headers;
}

/** 在代理建立失败且尚未开始响应时返回统一 502。 */
function writeProxyError(response: ServerResponse): void {
  if (response.headersSent || response.writableEnded) {
    response.destroy();
    return;
  }
  response.writeHead(502, { "Content-Type": "application/json" });
  response.end(
    JSON.stringify({
      success: false,
      data: null,
      error: "Agent Service unavailable",
    }),
  );
}

/** 转发一次请求，并直接 pipe 上下游以保留 POST body 与 SSE 分块。 */
async function proxyApiRequest(
  request: IncomingMessage,
  response: ServerResponse,
  options: AgentServiceProxyOptions,
): Promise<void> {
  const descriptor = options.descriptorPath
    ? await readAgentServiceDescriptor(options.descriptorPath)
    : null;
  const baseUrl = descriptor?.baseUrl ?? options.fallbackBaseUrl;
  const target = new URL(request.url ?? "/", `${baseUrl}/`);
  const upstream = http.request(
    target,
    {
      method: request.method,
      headers: upstreamHeaders(
        request.headers,
        target,
        descriptor?.credential ?? null,
      ),
    },
    (upstreamResponse) => {
      const headers = endToEndHeaders(upstreamResponse.headers);
      response.writeHead(
        upstreamResponse.statusCode ?? 502,
        upstreamResponse.statusMessage,
        headers,
      );
      upstreamResponse.pipe(response);
    },
  );
  upstream.on("error", () => writeProxyError(response));
  request.on("aborted", () => upstream.destroy());
  response.on("close", () => {
    if (!response.writableEnded) upstream.destroy();
  });
  request.pipe(upstream);
}

/** 创建可直接用于 Vite Connect 或 Node HTTP server 的动态代理。 */
export function createAgentServiceProxy(
  options: AgentServiceProxyOptions,
): AgentServiceProxy {
  return (request, response, next) => {
    const rawUrl = request.url ?? "/";
    if (!isApiRequest(rawUrl)) {
      if (next) next();
      else {
        response.writeHead(404);
        response.end();
      }
      return;
    }
    void proxyApiRequest(request, response, options).catch(() =>
      writeProxyError(response),
    );
  };
}
