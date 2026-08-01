/** 在隔离 preload 中只暴露白名单桌面能力，不泄露 Electron 或 Node 全局对象。 */

import { contextBridge, ipcRenderer } from "electron";
import { createDesktopBridge } from "./preloadBridge";

contextBridge.exposeInMainWorld(
  "trowelDesktop",
  createDesktopBridge((channel, ...args) => ipcRenderer.invoke(channel, ...args)),
);
