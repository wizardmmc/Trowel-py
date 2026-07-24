import type { Runtime } from "../../api/agent";

export interface RuntimeOption {
  readonly value: Runtime;
  readonly label: string;
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
    label: "Claude Code",
    native: "原生 claude -p",
    desc: "继续使用现有 CCHost 配置；保留 Workflow、hook 与 CC checkpoint。",
    efforts: [
      { value: "", label: "跟随" },
      { value: "low", label: "low" },
      { value: "medium", label: "medium" },
      { value: "high", label: "high" },
      { value: "xhigh", label: "xhigh" },
      { value: "max", label: "max" },
      { value: "ultracode", label: "ultracode" },
    ],
    permissions: [
      { value: "bypassPermissions", label: "跟随 CC（bypass）" },
      { value: "default", label: "default" },
      { value: "acceptEdits", label: "acceptEdits" },
    ],
  },
  {
    value: "codex",
    label: "Codex",
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

export const RUNTIME_LABEL: Record<Runtime, string> = {
  claude_code: "Claude Code",
  codex: "Codex",
};

export function runtimeOptionIndex(value: Runtime): number {
  return RUNTIME_OPTIONS.findIndex((option) => option.value === value);
}
