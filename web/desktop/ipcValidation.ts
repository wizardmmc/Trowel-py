/** 校验 renderer IPC 的来源以及允许交给操作系统处理的参数。 */

import path from "node:path";

export function isTrustedRendererUrl(
  senderUrl: string,
  rendererUrl: string,
  diagnosticUrl: string,
): boolean {
  if (senderUrl === diagnosticUrl) return true;
  try {
    const sender = new URL(senderUrl);
    const renderer = new URL(rendererUrl);
    if (renderer.protocol === "file:") return sender.href === renderer.href;
    return sender.origin === renderer.origin;
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
