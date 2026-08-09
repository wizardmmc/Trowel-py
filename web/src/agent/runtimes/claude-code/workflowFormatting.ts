/** 提供 Claude Code workflow 展示共用的纯文本格式化函数。 */

/** 把 workflow token 数量压缩为适合紧凑状态栏的文本。 */
export function formatWorkflowTokens(tokens: number): string {
  return tokens >= 1000 ? `${(tokens / 1000).toFixed(1)}k` : String(tokens);
}

/** 把 workflow 正文收敛成单行预览，并按字符上限截断。 */
export function briefWorkflowText(text: string, max = 48): string {
  const oneLine = text.replace(/\s+/g, " ").trim();
  return oneLine.length > max ? `${oneLine.slice(0, max - 1)}…` : oneLine;
}
