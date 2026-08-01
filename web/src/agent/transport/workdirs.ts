/** 定义并调用工作目录选择器使用的本地目录浏览接口。 */

export interface AgentDirectoryEntry {
  readonly name: string;
  readonly path: string;
}

interface ApiEnvelope<T> {
  readonly success: boolean;
  readonly data: T | null;
  readonly error: string | null;
}

/** 通过现有本地文件系统端点读取指定路径下的文件夹。 */
export async function listAgentDirectory(
  directory: string,
): Promise<readonly AgentDirectoryEntry[]> {
  const response = await fetch(
    `/api/cc/list-dir?path=${encodeURIComponent(directory)}`,
  );
  if (!response.ok) {
    throw new Error(`CC API error: ${response.status}`);
  }
  const result: ApiEnvelope<readonly AgentDirectoryEntry[]> =
    await response.json();
  if (!result.success || result.error) {
    throw new Error(result.error ?? "CC API call failed");
  }
  return result.data ?? [];
}
