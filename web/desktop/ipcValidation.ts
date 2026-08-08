/** 校验 renderer IPC 的来源以及允许交给操作系统处理的参数。 */

import path from "node:path";

export function isTrustedRendererUrl(
  senderUrl: string,
  rendererUrl: string,
  diagnosticUrl: string,
): boolean {
  if (senderUrl === diagnosticUrl) return true;
  return isTrustedRendererLocation(senderUrl, rendererUrl);
}

/**
 * 判断地址是否仍属于配置好的 renderer 页面。
 * 本地安装包只允许同一个 HTML 文件，但允许查询参数和页内锚点承载界面状态。
 */
export function isTrustedRendererLocation(
  candidateUrl: string,
  rendererUrl: string,
): boolean {
  try {
    const candidate = new URL(candidateUrl);
    const renderer = new URL(rendererUrl);
    if (renderer.protocol === "file:") {
      if (candidate.protocol !== "file:") return false;
      candidate.search = "";
      candidate.hash = "";
      renderer.search = "";
      renderer.hash = "";
      return candidate.href === renderer.href;
    }
    return candidate.origin === renderer.origin;
  } catch {
    return false;
  }
}

export function assertAllowedExternalUrl(rawUrl: string): string {
  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    throw new Error("unsupported external URL");
  }
  if (!["https:", "http:", "mailto:"].includes(url.protocol)) {
    throw new Error("unsupported external URL");
  }
  return url.toString();
}

export function assertPathInsideRoot(candidate: string, root: string): string {
  if (!path.isAbsolute(candidate) || !path.isAbsolute(root)) {
    throw new Error("local path and root must be absolute");
  }
  const resolvedCandidate = path.resolve(candidate);
  const resolvedRoot = path.resolve(root);
  const relative = path.relative(resolvedRoot, resolvedCandidate);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error("local path is outside allowed root");
  }
  return resolvedCandidate;
}
