/** 原子发布 Desktop 开发态 Agent Service 的连接描述。 */

import { randomUUID } from "node:crypto";
import {
  chmod,
  mkdir,
  readFile,
  rename,
  unlink,
  writeFile,
} from "node:fs/promises";
import path from "node:path";

export interface AgentServiceDescriptor {
  readonly serviceInstanceId: string;
  readonly baseUrl: string;
  readonly credential: string;
}

/** 校验 descriptor 只指向带凭据的本机 HTTP 服务。 */
function isAgentServiceDescriptor(
  value: unknown,
): value is AgentServiceDescriptor {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Record<string, unknown>;
  if (
    typeof candidate.serviceInstanceId !== "string" ||
    !candidate.serviceInstanceId ||
    typeof candidate.baseUrl !== "string" ||
    typeof candidate.credential !== "string" ||
    !candidate.credential
  ) {
    return false;
  }
  try {
    const endpoint = new URL(candidate.baseUrl);
    return (
      endpoint.protocol === "http:" &&
      ["127.0.0.1", "localhost"].includes(endpoint.hostname) &&
      endpoint.pathname === "/" &&
      !endpoint.search &&
      !endpoint.hash
    );
  } catch {
    return false;
  }
}

/** 读取当前有效 descriptor；不存在、写坏或越过 loopback 边界时返回 null。 */
export async function readAgentServiceDescriptor(
  filePath: string,
): Promise<AgentServiceDescriptor | null> {
  try {
    const value: unknown = JSON.parse(await readFile(filePath, "utf8"));
    return isAgentServiceDescriptor(value) ? value : null;
  } catch {
    return null;
  }
}

/** 以 0600 临时文件和同目录 rename 原子发布 descriptor。 */
export async function writeAgentServiceDescriptor(
  filePath: string,
  descriptor: AgentServiceDescriptor,
): Promise<void> {
  if (!isAgentServiceDescriptor(descriptor)) {
    throw new Error("invalid Agent Service descriptor");
  }
  const directory = path.dirname(filePath);
  const temporaryPath = `${filePath}.${process.pid}.${randomUUID()}.tmp`;
  await mkdir(directory, { recursive: true, mode: 0o700 });
  try {
    await writeFile(temporaryPath, `${JSON.stringify(descriptor)}\n`, {
      encoding: "utf8",
      mode: 0o600,
    });
    await chmod(temporaryPath, 0o600);
    await rename(temporaryPath, filePath);
    await chmod(filePath, 0o600);
  } finally {
    await unlink(temporaryPath).catch(() => undefined);
  }
}

/** 仅当文件仍声明属于目标实例时删除 descriptor。 */
export async function removeAgentServiceDescriptor(
  filePath: string,
  serviceInstanceId: string,
): Promise<void> {
  const current = await readAgentServiceDescriptor(filePath);
  if (current?.serviceInstanceId !== serviceInstanceId) return;
  await unlink(filePath).catch(() => undefined);
}
