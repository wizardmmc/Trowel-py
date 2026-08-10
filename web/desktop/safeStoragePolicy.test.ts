/** 验证自动 smoke 与正式应用使用不同的 macOS Safe Storage 策略。 */

import { expect, it, vi } from "vitest";
import {
  configureSafeStorageForSmoke,
  isAutomatedDesktopRun,
} from "./safeStoragePolicy";

it("uses a mock keychain only for the automated desktop smoke", () => {
  const appendSwitch = vi.fn();

  configureSafeStorageForSmoke({ appendSwitch }, true);

  expect(appendSwitch).toHaveBeenCalledOnce();
  expect(appendSwitch).toHaveBeenCalledWith("use-mock-keychain");
});

it("does not replace the system keychain for the normal application", () => {
  const appendSwitch = vi.fn();

  configureSafeStorageForSmoke({ appendSwitch }, false);

  expect(appendSwitch).not.toHaveBeenCalled();
});

it("treats only the explicit E2E environment value as an automated run", () => {
  expect(isAutomatedDesktopRun({ TROWEL_DESKTOP_E2E: "1" }, false)).toBe(true);
  expect(isAutomatedDesktopRun({ TROWEL_DESKTOP_E2E: "0" }, false)).toBe(false);
  expect(isAutomatedDesktopRun({}, true)).toBe(true);
});
