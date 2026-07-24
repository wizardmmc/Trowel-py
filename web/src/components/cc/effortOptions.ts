export interface EffortOption {
  readonly value: string;
  readonly description: string;
  readonly tag?: string;
}

export const EFFORT_OPTIONS: readonly EffortOption[] = [
  { value: "low", description: "快速直接，简单改动" },
  { value: "medium", description: "平衡，标准测试覆盖" },
  { value: "high", description: "深入实现，详尽测试" },
  {
    value: "max",
    description: "最强推理（cc：Opus 专属，其它自动降级 high）",
  },
  { value: "auto", description: "用模型默认强度" },
  {
    value: "ultracode",
    description: "xhigh + 自动多 agent 编排（cc 2.1.197+）",
    tag: "GLM 后端 xhigh 支持性待实测 · cc 自动降级兜底",
  },
];
