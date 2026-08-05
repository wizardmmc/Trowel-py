/** 从本地 Agent Service 的非成功响应中提取可直接展示的错误原因。 */

/** 优先读取结构化错误字段，无法解析时回退到带状态码的稳定说明。 */
export async function readHttpError(
  response: Response,
  fallbackPrefix: string,
): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object") {
      const payload = body as Record<string, unknown>;
      for (const key of ["error", "detail"] as const) {
        const value = payload[key];
        if (typeof value === "string" && value.trim()) return value;
        if (value && typeof value === "object") {
          const message = (value as Record<string, unknown>).message;
          if (typeof message === "string" && message.trim()) return message;
        }
      }
    }
  } catch {
    // 非 JSON 错误页仍由下面的 HTTP 状态说明兜底。
  }
  return `${fallbackPrefix}: ${response.status}`;
}
