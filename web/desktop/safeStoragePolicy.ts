/** 只在显式自动化桌面运行中替换 Electron Safe Storage 的系统钥匙串。 */

export interface CommandLineSwitches {
  appendSwitch(name: string): void;
}

/** 判断本次 Electron 启动是否属于 smoke 或行为 E2E。 */
export function isAutomatedDesktopRun(
  environment: Readonly<Record<string, string | undefined>>,
  smoke: boolean,
): boolean {
  return smoke || environment.TROWEL_DESKTOP_E2E === "1";
}

export function configureSafeStorageForSmoke(
  commandLine: CommandLineSwitches,
  automatedSmoke: boolean,
): void {
  if (automatedSmoke) commandLine.appendSwitch("use-mock-keychain");
}
