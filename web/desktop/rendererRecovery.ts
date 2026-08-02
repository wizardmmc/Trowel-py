/** 决定 Electron renderer 异常退出后自动重载还是转入诊断页。 */

export type DesktopPage = "renderer" | "diagnostics" | null;
export type RendererRecoveryAction = "reload" | "diagnostics" | "none";

const AUTO_RECOVERY_WINDOW_MS = 60_000;

export interface RendererRecoveryInput {
  readonly crashedPage: DesktopPage;
  readonly finalQuit: boolean;
  readonly reason: string;
  readonly previousCrashAt: number | null;
  readonly now: number;
}

export function decideRendererRecovery(
  input: RendererRecoveryInput,
): RendererRecoveryAction {
  if (
    input.finalQuit ||
    input.crashedPage !== "renderer" ||
    input.reason === "clean-exit"
  ) {
    return "none";
  }
  if (
    input.previousCrashAt !== null &&
    input.now - input.previousCrashAt <= AUTO_RECOVERY_WINDOW_MS
  ) {
    return "diagnostics";
  }
  return "reload";
}
