/** 定义跨设置页和会话页复用的原生 Runtime 思考强度。 */

/** Claude CLI 当前公开支持的显式思考强度。 */
export const CLAUDE_EFFORT_LEVELS = [
  "low",
  "medium",
  "high",
  "xhigh",
  "max",
] as const;

/** Claude 会话可选值；空字符串表示不覆盖 Runtime 默认值。 */
export const CLAUDE_SESSION_EFFORTS = ["", ...CLAUDE_EFFORT_LEVELS] as const;
