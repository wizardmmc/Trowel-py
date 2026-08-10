/** 验证 E2E API client 只访问当前 Electron 发布的私有 sidecar。 */

import { afterEach, describe, expect, mock, test } from "bun:test";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { DesktopApiClient, waitForDesktopApi } from "./api-client.mjs";

const originalFetch = globalThis.fetch;
const temporaryRoots = [];

afterEach(async () => {
  globalThis.fetch = originalFetch;
  await Promise.all(
    temporaryRoots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});

describe("DesktopApiClient", () => {
  test("adds the descriptor credential and unwraps the success envelope", async () => {
    globalThis.fetch = mock(async (url, options) => {
      expect(url).toBe("http://127.0.0.1:43123/api/desktop/resources");
      expect(new Headers(options.headers).get("Authorization")).toBe(
        "Bearer descriptor-secret",
      );
      return Response.json({ success: true, data: { resources: [] }, error: null });
    });
    const client = new DesktopApiClient({
      baseUrl: "http://127.0.0.1:43123",
      credential: "descriptor-secret",
      serviceInstanceId: "instance-1",
    });

    expect(await client.get("/api/desktop/resources")).toEqual({ resources: [] });
  });

  test("keeps the structured API error code", async () => {
    globalThis.fetch = mock(async () =>
      Response.json(
        {
          success: false,
          data: null,
          error: { code: "turn_conflict", message: "turn already running" },
        },
        { status: 409 },
      ),
    );
    const client = new DesktopApiClient({
      baseUrl: "http://127.0.0.1:43123",
      credential: "descriptor-secret",
      serviceInstanceId: "instance-1",
    });

    await expect(client.post("/api/agent/sessions/s1/turns", { text: "next" }))
      .rejects.toMatchObject({ status: 409, code: "turn_conflict" });
  });

  test("waits for a rotated credential instead of accepting the stale descriptor", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "trowel-api-test-"));
    temporaryRoots.push(root);
    const descriptorPath = path.join(root, "agent-service.json");
    await writeFile(
      descriptorPath,
      JSON.stringify({
        base_url: "http://127.0.0.1:43123",
        credential: "stale",
        service_instance_id: "instance-stale",
      }),
      "utf8",
    );
    globalThis.fetch = mock(async () =>
      Response.json({ success: true, data: { ready: true }, error: null }),
    );
    const replacement = new Promise((resolve) => {
      setTimeout(() => {
        void writeFile(
          descriptorPath,
          JSON.stringify({
            base_url: "http://127.0.0.1:43124",
            credential: "fresh",
            service_instance_id: "instance-fresh",
          }),
          "utf8",
        ).then(resolve);
      }, 25);
    });

    const client = await waitForDesktopApi(descriptorPath, {
      timeoutMs: 500,
      previousCredential: "stale",
    });

    await replacement;
    expect(client.credential).toBe("fresh");
    expect(client.baseUrl).toBe("http://127.0.0.1:43124");
  });
});
