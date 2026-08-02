/** 验证 Vite 开发代理动态跟随 Desktop Agent Service。 */

// @vitest-environment node

import {
  createServer,
  request as requestHttp,
  type IncomingHttpHeaders,
  type Server,
} from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { createAgentServiceProxy } from "./agentServiceProxy";
import { writeAgentServiceDescriptor } from "./serviceDescriptor";

const servers: Server[] = [];
const temporaryDirectories: string[] = [];

/** 监听随机 loopback 端口并返回服务 origin。 */
async function listen(server: Server): Promise<string> {
  servers.push(server);
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("missing port");
  return `http://127.0.0.1:${address.port}`;
}

/** 创建当前用例的 descriptor 文件路径。 */
async function descriptorPath(): Promise<string> {
  const directory = await mkdtemp(path.join(os.tmpdir(), "trowel-proxy-test-"));
  temporaryDirectories.push(directory);
  return path.join(directory, "agent-service.json");
}

/** 发出可控制 Connection 头的真实 Node HTTP 请求。 */
async function requestText(
  url: string,
  body: string,
): Promise<{
  readonly status: number;
  readonly headers: IncomingHttpHeaders;
  readonly body: string;
}> {
  return new Promise((resolve, reject) => {
    const request = requestHttp(
      url,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Connection: "x-remove",
          "X-Remove": "request-only",
        },
      },
      (response) => {
        response.setEncoding("utf8");
        let responseBody = "";
        response.on("data", (chunk) => {
          responseBody += chunk;
        });
        response.on("end", () => {
          resolve({
            status: response.statusCode ?? 0,
            headers: response.headers,
            body: responseBody,
          });
        });
      },
    );
    request.once("error", reject);
    request.end(body);
  });
}

afterEach(async () => {
  await Promise.all(
    servers.splice(0).map(
      (server) => new Promise<void>((resolve) => server.close(() => resolve())),
    ),
  );
  await Promise.all(
    temporaryDirectories.splice(0).map((directory) =>
      rm(directory, { recursive: true, force: true }),
    ),
  );
});

describe("Agent Service development proxy", () => {
  it("forwards POST bodies, private credentials and SSE response bytes", async () => {
    let receivedBody = "";
    let receivedAuthorization = "";
    let receivedConnectionOnlyHeader = "";
    const upstream = createServer((request, response) => {
      receivedAuthorization = String(request.headers.authorization ?? "");
      receivedConnectionOnlyHeader = String(request.headers["x-remove"] ?? "");
      request.setEncoding("utf8");
      request.on("data", (chunk) => {
        receivedBody += chunk;
      });
      request.on("end", () => {
        response.writeHead(200, {
          "Content-Type": "text/event-stream",
          Connection: "x-upstream-only",
          "X-Upstream-Only": "response-only",
        });
        response.write("data: first\n\n");
        response.end("data: second\n\n");
      });
    });
    const upstreamUrl = await listen(upstream);
    const filePath = await descriptorPath();
    await writeAgentServiceDescriptor(filePath, {
      serviceInstanceId: "instance-1",
      baseUrl: upstreamUrl,
      credential: "desktop-secret",
    });
    const proxy = createServer(
      createAgentServiceProxy({
        descriptorPath: filePath,
        fallbackBaseUrl: "http://127.0.0.1:9",
      }),
    );
    const proxyUrl = await listen(proxy);

    const response = await requestText(
      `${proxyUrl}/api/agent/sessions`,
      JSON.stringify({ workdir: "/repo" }),
    );

    expect(response.status).toBe(200);
    expect(response.headers["content-type"]).toContain("text/event-stream");
    expect(response.headers["x-upstream-only"]).toBeUndefined();
    expect(response.body).toBe("data: first\n\ndata: second\n\n");
    expect(JSON.parse(receivedBody)).toEqual({ workdir: "/repo" });
    expect(receivedAuthorization).toBe("Bearer desktop-secret");
    expect(receivedConnectionOnlyHeader).toBe("");
  });

  it("reads the descriptor again for each request", async () => {
    const firstUrl = await listen(
      createServer((_request, response) => response.end("first")),
    );
    const secondUrl = await listen(
      createServer((_request, response) => response.end("second")),
    );
    const filePath = await descriptorPath();
    await writeAgentServiceDescriptor(filePath, {
      serviceInstanceId: "instance-1",
      baseUrl: firstUrl,
      credential: "secret-1",
    });
    const proxyUrl = await listen(
      createServer(
        createAgentServiceProxy({
          descriptorPath: filePath,
          fallbackBaseUrl: "http://127.0.0.1:9",
        }),
      ),
    );

    expect(await (await fetch(`${proxyUrl}/api/health`)).text()).toBe("first");
    await writeAgentServiceDescriptor(filePath, {
      serviceInstanceId: "instance-2",
      baseUrl: secondUrl,
      credential: "secret-2",
    });
    expect(await (await fetch(`${proxyUrl}/api/health`)).text()).toBe("second");
  });
});
