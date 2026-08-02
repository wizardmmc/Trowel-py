/** 只在自动化桌面 smoke 中替换 Electron Safe Storage 的系统钥匙串。 */

export interface CommandLineSwitches {
  appendSwitch(name: string): void;
}

export function configureSafeStorageForSmoke(
  commandLine: CommandLineSwitches,
  automatedSmoke: boolean,
): void {
  if (automatedSmoke) commandLine.appendSwitch("use-mock-keychain");
}
