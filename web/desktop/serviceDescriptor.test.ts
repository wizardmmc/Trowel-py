/** 验证 Desktop 发布给开发代理的 Agent Service 描述文件。 */

// @vitest-environment node

import { mkdtemp, readdir, stat, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import {
  readAgentServiceDescriptor,
  removeAgentServiceDescriptor,
  writeAgentServiceDescriptor,
} from "./serviceDescriptor";

const temporaryDirectories: string[] = [];

/** 创建本用例独享的 descriptor 路径。 */
async function descriptorPath(): Promise<string> {
  const directory = await mkdtemp(path.join(os.tmpdir(), "trowel-service-test-"));
  temporaryDirectories.push(directory);
  return path.join(directory, "agent-service.json");
}

afterEach(async () => {
  const { rm } = await import("node:fs/promises");
  await Promise.all(
    temporaryDirectories.splice(0).map((directory) =>
      rm(directory, { recursive: true, force: true }),
    ),
  );
});

describe("Agent Service descriptor", () => {
  it("publishes a validated descriptor atomically with owner-only access", async () => {
    const filePath = await descriptorPath();
    const descriptor = {
      serviceInstanceId: "instance-1",
      baseUrl: "http://127.0.0.1:43123",
      credential: "desktop-secret",
    };

    await writeAgentServiceDescriptor(filePath, descriptor);

    await expect(readAgentServiceDescriptor(filePath)).resolves.toEqual(descriptor);
    expect((await stat(filePath)).mode & 0o777).toBe(0o600);
    expect(await readdir(path.dirname(filePath))).toEqual(["agent-service.json"]);
  });

  it("removes only the descriptor published by the expected instance", async () => {
    const filePath = await descriptorPath();
    await writeAgentServiceDescriptor(filePath, {
      serviceInstanceId: "new-instance",
      baseUrl: "http://127.0.0.1:43123",
      credential: "desktop-secret",
    });

    await removeAgentServiceDescriptor(filePath, "old-instance");
    expect(await readAgentServiceDescriptor(filePath)).not.toBeNull();

    await removeAgentServiceDescriptor(filePath, "new-instance");
    expect(await readAgentServiceDescriptor(filePath)).toBeNull();
  });

  it("ignores malformed or non-loopback descriptors", async () => {
    const filePath = await descriptorPath();
    await writeFile(
      filePath,
      JSON.stringify({
        serviceInstanceId: "instance-1",
        baseUrl: "https://example.com",
        credential: "desktop-secret",
      }),
      "utf8",
    );

    await expect(readAgentServiceDescriptor(filePath)).resolves.toBeNull();
  });
});
