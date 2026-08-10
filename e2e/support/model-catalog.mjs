/** 提供只监听 loopback 的 OpenAI 风格模型目录，供真实配置 API 完成 seed。 */

import { createServer } from "node:http";

/** 启动仅返回版本化测试模型 ID 的本地目录服务。 */
export async function startModelCatalogServer() {
  const server = createServer((request, response) => {
    if (request.method !== "GET" || request.url !== "/v1/models") {
      response.writeHead(404).end();
      return;
    }
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(
      JSON.stringify({
        data: [
          { id: "e2e-claude-model" },
          { id: "gpt-5.6-sol" },
        ],
      }),
    );
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string") {
    server.close();
    throw new Error("model catalog did not bind a TCP port");
  }
  return {
    baseUrl: `http://127.0.0.1:${address.port}/v1`,
    /** 停止接收新连接，并等待当前目录请求结束。 */
    close: () => new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve())),
  };
}
