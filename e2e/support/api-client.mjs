/** 读取 Desktop descriptor，并以同一认证边界调用生产 HTTP API。 */

import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";

/** 保留 HTTP 状态和统一错误码的 E2E API 错误。 */
export class DesktopApiError extends Error {
  /** @param {number} status HTTP 状态码。 @param {string|null} code 稳定错误码。 */
  constructor(status, code) {
    super(`desktop API request failed with status ${status}${code ? ` (${code})` : ""}`);
    this.name = "DesktopApiError";
    this.status = status;
    this.code = code;
  }
}

/** 只持有当前测试实例的 loopback 地址和凭据。 */
export class DesktopApiClient {
  /** @param {{baseUrl: string, credential: string, serviceInstanceId: string}} descriptor 当前 Host descriptor。 */
  constructor(descriptor) {
    const endpoint = new URL(descriptor.baseUrl);
    if (endpoint.protocol !== "http:" || endpoint.hostname !== "127.0.0.1") {
      throw new Error("desktop E2E API must use a 127.0.0.1 HTTP endpoint");
    }
    if (!descriptor.credential) throw new Error("desktop descriptor credential is required");
    if (!descriptor.serviceInstanceId) {
      throw new Error("desktop descriptor service instance ID is required");
    }
    this.baseUrl = endpoint.origin;
    this.credential = descriptor.credential;
    this.appInstanceIdentity = createHash("sha256")
      .update(descriptor.serviceInstanceId, "utf8")
      .digest("hex")
      .slice(0, 20);
  }

  /** @param {string} route API 路径。 */
  get(route) {
    return this.request(route, { method: "GET" });
  }

  /** @param {string} route API 路径。 @param {unknown} body JSON 请求体。 */
  post(route, body = undefined) {
    return this.request(route, { method: "POST", body });
  }

  /** @param {string} route API 路径。 @param {unknown} body JSON 请求体。 */
  put(route, body) {
    return this.request(route, { method: "PUT", body });
  }

  /** @param {string} route API 路径。 */
  delete(route) {
    return this.request(route, { method: "DELETE" });
  }

  /** 执行一次有界请求并解开 Trowel 统一 envelope。 */
  async request(route, { method, body = undefined }) {
    if (!route.startsWith("/")) throw new Error("desktop API route must be absolute");
    const headers = new Headers({ Authorization: `Bearer ${this.credential}` });
    if (body !== undefined) headers.set("Content-Type", "application/json");
    const response = await fetch(new URL(route, `${this.baseUrl}/`).toString(), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(20_000),
    });
    let envelope;
    try {
      envelope = await response.json();
    } catch {
      throw new DesktopApiError(response.status, "invalid_json");
    }
    if (!response.ok || envelope?.success !== true) {
      const error = envelope?.error;
      const code = typeof error === "object" && error ? error.code ?? null : null;
      throw new DesktopApiError(response.status, code);
    }
    return envelope.data;
  }
}

/**
 * 等待 Host 原子发布 descriptor，再构造当前实例 client。
 *
 * @param {string} descriptorPath Host descriptor 路径。
 * @param {{timeoutMs?: number, previousCredential?: string|null}|number} options
 *   等待预算；重启场景同时拒绝旧进程遗留的 credential。
 */
export async function waitForDesktopApi(descriptorPath, options = {}) {
  const normalized = typeof options === "number" ? { timeoutMs: options } : options;
  const timeoutMs = normalized.timeoutMs ?? 20_000;
  const previousCredential = normalized.previousCredential ?? null;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const descriptor = JSON.parse(await readFile(descriptorPath, "utf8"));
      if (descriptor.credential === previousCredential) {
        throw new Error("desktop descriptor still belongs to the previous Host");
      }
      const client = new DesktopApiClient({
        baseUrl: descriptor.base_url ?? descriptor.baseUrl,
        credential: descriptor.credential,
        serviceInstanceId:
          descriptor.service_instance_id ?? descriptor.serviceInstanceId,
      });
      await client.get("/api/desktop/readiness");
      return client;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  }
  throw new Error("desktop sidecar descriptor did not become ready");
}
