/** 验证自动 smoke 与正式应用使用不同的 macOS Safe Storage 策略。 */

import { expect, it, vi } from "vitest";
import { configureSafeStorageForSmoke } from "./safeStoragePolicy";

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
