
export function getDisplayPath(filePath: string, workdir?: string): string {
  if (!workdir) return filePath;
  const rel = relativeIfInside(workdir, filePath);
  return rel ?? filePath;
}

function relativeIfInside(workdir: string, filePath: string): string | null {
  const base = workdir.endsWith("/") ? workdir.slice(0, -1) : workdir;
  if (filePath === base) return "";
  const prefix = base + "/";
  if (filePath.startsWith(prefix)) {
    return filePath.slice(prefix.length);
  }
  return null;
}
