/** 解析正式 App、日常开发和隔离开发使用的桌面数据目录。 */

import path from "node:path";

export type DesktopDataMode = "packaged" | "canonical-dev" | "isolated-dev";

interface DesktopPathInput {
  readonly appDataDirectory: string;
  readonly logsDirectory: string;
  readonly mode: DesktopDataMode;
  readonly dataDirectoryOverride?: string;
  readonly logDirectoryOverride?: string;
  readonly electronUserDataDirectoryOverride?: string;
}

interface DesktopPaths {
  readonly dataDirectory: string;
  readonly logDirectory: string;
  readonly electronUserDataDirectory: string | null;
}

/** 根据是否打包和开发参数选择唯一受支持的数据模式。 */
export function resolveDesktopDataMode(
  requested: string | undefined,
  packaged: boolean,
): DesktopDataMode {
  if (packaged) return "packaged";
  if (!requested || requested === "canonical-dev") return "canonical-dev";
  if (requested === "isolated-dev") return "isolated-dev";
  throw new Error(`Unsupported desktop data mode: ${requested}`);
}

/** 返回业务数据、日志和可选 Electron userData 的绝对目录。 */
export function resolveDesktopPaths(input: DesktopPathInput): DesktopPaths {
  const isolatedRoot = path.join(input.appDataDirectory, "Trowel Dev");
  const defaultDataDirectory =
    input.mode === "isolated-dev"
      ? path.join(isolatedRoot, "data")
      : path.join(input.appDataDirectory, "Trowel", "data");
  const defaultLogDirectory =
    input.mode === "isolated-dev"
      ? path.join(isolatedRoot, "logs")
      : input.logsDirectory;
  const defaultElectronUserData =
    input.mode === "isolated-dev"
      ? path.join(isolatedRoot, "electron")
      : null;

  return {
    dataDirectory: path.resolve(
      input.dataDirectoryOverride ?? defaultDataDirectory,
    ),
    logDirectory: path.resolve(
      input.logDirectoryOverride ?? defaultLogDirectory,
    ),
    electronUserDataDirectory: input.electronUserDataDirectoryOverride
      ? path.resolve(input.electronUserDataDirectoryOverride)
      : defaultElectronUserData,
  };
}
