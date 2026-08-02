/** 定义 Trowel 应用菜单和系统状态菜单，不直接持有 Electron 全局对象。 */

import type { MenuItemConstructorOptions } from "electron";

export interface DesktopMenuActions {
  readonly openTrowel: () => void;
  readonly openDiagnostics: () => void;
  readonly openLogs: () => void;
  readonly quitTrowel: () => void;
}

export interface DesktopMenuItem {
  readonly label?: string;
  readonly role?: MenuItemConstructorOptions["role"];
  readonly type?: "normal" | "separator";
  readonly accelerator?: string;
  readonly click?: () => void;
  readonly submenu?: readonly DesktopMenuItem[];
}

/** 返回正式应用菜单；首项名称和退出命令始终使用产品名。 */
export function applicationMenuTemplate(
  appName: string,
  actions: DesktopMenuActions,
): DesktopMenuItem[] {
  return [
    {
      label: appName,
      submenu: [
        { role: "about", label: `关于 ${appName}` },
        { type: "separator" },
        { role: "services" },
        { type: "separator" },
        { role: "hide", label: `隐藏 ${appName}` },
        { role: "hideOthers", label: "隐藏其他" },
        { role: "unhide", label: "全部显示" },
        { type: "separator" },
        {
          label: `退出 ${appName}`,
          accelerator: "CommandOrControl+Q",
          click: actions.quitTrowel,
        },
      ],
    },
    { role: "editMenu" },
    { role: "viewMenu" },
    { role: "windowMenu" },
  ];
}

/** 返回 macOS 顶部右侧状态图标的固定产品命令。 */
export function statusMenuTemplate(
  actions: DesktopMenuActions,
): DesktopMenuItem[] {
  return [
    { label: "打开 Trowel", click: actions.openTrowel },
    { label: "打开诊断", click: actions.openDiagnostics },
    { label: "打开日志目录", click: actions.openLogs },
    { type: "separator" },
    { label: "退出 Trowel", click: actions.quitTrowel },
  ];
}
