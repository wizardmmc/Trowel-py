/** 定义 Codex 权限预设及其展示顺序。 */

export type PermissionPreset =
  | "follow"
  | "read-only"
  | "workspace-write"
  | "danger-full-access";

export const PRESET_LABELS: Record<PermissionPreset, string> = {
  follow: "Follow",
  "read-only": "Read only",
  "workspace-write": "Workspace write",
  "danger-full-access": "Full access",
};

export const PRESET_ORDER: readonly PermissionPreset[] = [
  "follow",
  "read-only",
  "workspace-write",
  "danger-full-access",
];

// 活动会话菜单不含 follow：sticky turn override 后没有确定的恢复语义。
export const ACTIVE_SESSION_PRESETS: readonly PermissionPreset[] = [
  "read-only",
  "workspace-write",
  "danger-full-access",
];
