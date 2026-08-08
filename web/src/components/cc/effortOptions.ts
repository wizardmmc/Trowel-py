/** 定义 Claude Code 会话可选的推理强度文案。 */

export interface EffortOption {
  readonly value: string;
  readonly description: string;
  readonly tag?: string;
}

export const EFFORT_OPTIONS: readonly EffortOption[] = [
  { value: "low", description: "快速直接，简单改动" },
  { value: "medium", description: "平衡，标准测试覆盖" },
  { value: "high", description: "深入实现，详尽测试" },
  { value: "xhigh", description: "更深入的推理与验证" },
  {
    value: "max",
    description: "Claude CLI 提供的最高推理强度",
  },
];
