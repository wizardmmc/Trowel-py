/** 定义新会话对话框使用的 runtime 文案和 effort 选项。 */

import type { Runtime } from "../../agent/transport";
import { CLAUDE_SESSION_EFFORTS } from "../../agent/ui/connectionSessionConfig";

export interface RuntimeOption {
  readonly value: Runtime;
  readonly native: string;
  readonly desc: string;
  readonly efforts: ReadonlyArray<{
    readonly value: string;
    readonly label: string;
  }>;
  readonly permissions: ReadonlyArray<{
    readonly value: string;
    readonly label: string;
  }>;
}

export const RUNTIME_OPTIONS: readonly RuntimeOption[] = [
  {
    value: "claude_code",
    native: "原生 claude -p",
    desc: "继续使用现有 Claude Host 配置；保留 Workflow、hook 与 Claude checkpoint。",
    efforts: CLAUDE_SESSION_EFFORTS.map((value) => ({
      value,
      label: value || "跟随",
    })),
    permissions: [
      { value: "bypassPermissions", label: "跟随 Claude（bypass）" },
      { value: "default", label: "default" },
      { value: "acceptEdits", label: "acceptEdits" },
      { value: "dontAsk", label: "dontAsk" },
    ],
  },
  {
    value: "codex",
    native: "原生 app-server",
    desc: "使用本机 Codex 订阅、sandbox、审批与 usage；不经过 Claude Code。",
    efforts: [],
    permissions: [
      { value: "follow", label: "跟随 Codex" },
      { value: "read-only", label: "read-only" },
      { value: "workspace-write", label: "workspace-write" },
      { value: "danger-full-access", label: "Full access" },
    ],
  },
];

export function runtimeOptionIndex(value: Runtime): number {
  return RUNTIME_OPTIONS.findIndex((option) => option.value === value);
}
