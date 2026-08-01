/** 向 Agent UI 提供工作目录浏览能力，不暴露底层 HTTP 调用。 */

import {
  listAgentDirectory,
  type AgentDirectoryEntry,
} from "../transport/workdirs";

export type { AgentDirectoryEntry } from "../transport/workdirs";

/** 读取目录选择器当前路径下的文件夹。 */
export function listWorkdirEntries(
  directory: string,
): Promise<readonly AgentDirectoryEntry[]> {
  return listAgentDirectory(directory);
}
