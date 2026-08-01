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
