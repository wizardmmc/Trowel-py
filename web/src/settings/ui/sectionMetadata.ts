/** 集中定义设置二级导航与顶部栏共用的分组、标题和说明。 */

import type { SettingsSection } from "../domain/types";

export const SETTINGS_SECTIONS: readonly {
  readonly id: SettingsSection;
  readonly label: string;
  readonly detail: string;
  readonly group: "configuration" | "system";
}[] = [
  { id: "paths", label: "存储与路径", detail: "真实数据位置", group: "configuration" },
  { id: "connections", label: "模型连接", detail: "连接与凭据", group: "configuration" },
  { id: "tasks", label: "后台任务", detail: "逐项绑定配置", group: "configuration" },
  { id: "agent", label: "Agent 默认", detail: "新会话条件", group: "configuration" },
  { id: "diagnostics", label: "连接诊断", detail: "三层状态", group: "system" },
  { id: "about", label: "关于", detail: "版本与数据原则", group: "system" },
] as const;
