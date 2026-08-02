/** 验证正式应用菜单与 macOS 右上角状态菜单的产品命令。 */

// @vitest-environment node

import { expect, it, vi } from "vitest";
import {
  applicationMenuTemplate,
  statusMenuTemplate,
  type DesktopMenuActions,
} from "./desktopMenus";

function actions(): DesktopMenuActions {
  return {
    openTrowel: vi.fn(),
    openDiagnostics: vi.fn(),
    openLogs: vi.fn(),
    quitTrowel: vi.fn(),
  };
}

it("uses Trowel as the application menu name and quit command", () => {
  const template = applicationMenuTemplate("Trowel", actions());
  const serialized = JSON.stringify(template);

  expect(template[0]?.label).toBe("Trowel");
  expect(serialized).toContain("退出 Trowel");
  expect(serialized).not.toContain("Electron");
  expect(serialized).not.toContain("trowel-desktop");
});

it("exposes all required status menu actions", () => {
  const menuActions = actions();
  const template = statusMenuTemplate(menuActions);
  const commands = template.filter((item) => item.type !== "separator");

  expect(commands.map((item) => item.label)).toEqual([
    "打开 Trowel",
    "打开诊断",
    "打开日志目录",
    "退出 Trowel",
  ]);
  for (const command of commands) command.click?.();
  expect(menuActions.openTrowel).toHaveBeenCalledOnce();
  expect(menuActions.openDiagnostics).toHaveBeenCalledOnce();
  expect(menuActions.openLogs).toHaveBeenCalledOnce();
  expect(menuActions.quitTrowel).toHaveBeenCalledOnce();
});
