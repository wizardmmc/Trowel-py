/** 向 Agent UI 提供工作目录浏览能力，不暴露底层 HTTP 调用。 */

import {
  listAgentDirectory,
  listRecentWorkspaceRecords,
  rememberRecentWorkspaceRecord,
  type AgentDirectoryEntry,
  type RecentWorkspaceWire,
} from "../transport/workdirs";

export type { AgentDirectoryEntry } from "../transport/workdirs";

export interface RecentWorkspace {
  readonly path: string;
  readonly name: string;
  readonly lastOpenedAt: string;
  readonly available: boolean;
}

function toRecentWorkspace(record: RecentWorkspaceWire): RecentWorkspace {
  return {
    path: record.path,
    name: record.name,
    lastOpenedAt: record.last_opened_at,
    available: record.available,
  };
}

/** 读取目录选择器当前路径下的文件夹。 */
export function listWorkdirEntries(
  directory: string,
): Promise<readonly AgentDirectoryEntry[]> {
  return listAgentDirectory(directory);
}

/** 读取所有 renderer 共用的 Recent 工作区。 */
export async function listRecentWorkspaces(): Promise<
  readonly RecentWorkspace[]
> {
  const records = await listRecentWorkspaceRecords();
  return records.map(toRecentWorkspace);
}

/** 保存并返回用户刚确认打开的 Recent 工作区。 */
export async function rememberRecentWorkspace(
  path: string,
): Promise<RecentWorkspace> {
  return toRecentWorkspace(await rememberRecentWorkspaceRecord(path));
}
