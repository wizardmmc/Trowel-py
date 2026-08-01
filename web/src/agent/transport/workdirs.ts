/** 定义并调用工作目录选择器使用的本地目录浏览接口。 */

import { transportFetch } from "../../platform/transport";
import { readHttpError } from "./httpError";

export interface AgentDirectoryEntry {
  readonly name: string;
  readonly path: string;
}

export interface RecentWorkspaceWire {
  readonly path: string;
  readonly name: string;
  readonly last_opened_at: string;
  readonly available: boolean;
}

interface ApiEnvelope<T> {
  readonly success: boolean;
  readonly data: T | null;
  readonly error: string | null;
}

async function requestWorkdirApi<T>(
  url: string,
  options?: RequestInit,
): Promise<T> {
  const response = await transportFetch(url, options);
  if (!response.ok) {
    throw new Error(await readHttpError(response, "Agent workspace API error"));
  }
  const result: ApiEnvelope<T> = await response.json();
  if (!result.success || result.error || result.data === null) {
    throw new Error(result.error ?? "Agent workspace API call failed");
  }
  return result.data;
}

/** 通过现有本地文件系统端点读取指定路径下的文件夹。 */
export async function listAgentDirectory(
  directory: string,
): Promise<readonly AgentDirectoryEntry[]> {
  const response = await transportFetch(
    `/api/cc/list-dir?path=${encodeURIComponent(directory)}`,
  );
  if (!response.ok) {
    throw new Error(await readHttpError(response, "Claude API 请求失败"));
  }
  const result: ApiEnvelope<readonly AgentDirectoryEntry[]> =
    await response.json();
  if (!result.success || result.error) {
    throw new Error(result.error ?? "Claude API 请求失败");
  }
  return result.data ?? [];
}

/** 读取由 Agent Service 稳定保存的 Recent 工作区。 */
export function listRecentWorkspaceRecords(): Promise<
  readonly RecentWorkspaceWire[]
> {
  return requestWorkdirApi("/api/agent/workspaces/recent");
}

/** 把用户确认打开的目录写入 Agent Service 的 Recent。 */
export function rememberRecentWorkspaceRecord(
  path: string,
): Promise<RecentWorkspaceWire> {
  return requestWorkdirApi("/api/agent/workspaces/recent", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
}
