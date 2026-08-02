/** 保留 renderer 的本地偏好，同时禁止 Electron session 持久化 Cookie。 */

import type { Session } from "electron";

export async function configureRendererSession(
  rendererSession: Session,
): Promise<void> {
  /** 先清理旧候选可能留下的 Cookie，再阻止后续响应重新写入。 */
  await rendererSession.clearStorageData({ storages: ["cookies"] });
  rendererSession.webRequest.onHeadersReceived((details, callback) => {
    if (!details.responseHeaders) {
      callback({});
      return;
    }
    callback({
      responseHeaders: removeCookieHeaders(details.responseHeaders),
    });
  });
}

export function removeCookieHeaders(
  headers: Readonly<Record<string, readonly string[]>>,
): Record<string, string[]> {
  /** Chromium 的响应头名称大小写不固定，两种历史 Cookie 头都必须过滤。 */
  return Object.fromEntries(
    Object.entries(headers)
      .filter(([name]) => !["set-cookie", "set-cookie2"].includes(name.toLowerCase()))
      .map(([name, values]) => [name, [...values]]),
  );
}
