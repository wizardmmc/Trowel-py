/**
 * CC-style file-path display (slice-029, mirrors `utils/file.ts::getDisplayPath`).
 *
 * CC does NOT use a depth/segment limit. It shows the path relative to the
 * session's cwd when the file is inside it; otherwise the absolute path. This
 * drops the prior char-based ellipsis (`/Users/.../do…`) which hid the most
 * useful part of the path (the project-relative tail).
 *
 * The browser can't read `$HOME`, so the `~/` step CC uses for files outside
 * cwd-but-inside-home is skipped — files outside the workdir show as absolute,
 * same as CC's last-resort branch.
 */

/**
 * Return `filePath` made relative to `workdir` when it lives inside workdir;
 * otherwise the absolute path. Empty/undefined workdir → absolute (used by
 * tests and any caller without a known session cwd).
 */
export function getDisplayPath(filePath: string, workdir?: string): string {
  if (!workdir) return filePath;
  const rel = relativeIfInside(workdir, filePath);
  return rel ?? filePath;
}

/**
 * POSIX `path.relative(workdir, filePath)` but only when the result stays
 * inside workdir (no `..` prefix). Returns null when filePath is outside or
 * equal to workdir (so the caller falls back to absolute).
 */
function relativeIfInside(workdir: string, filePath: string): string | null {
  // Normalize trailing slashes so `/a/b` and `/a/b/` both match files inside.
  const base = workdir.endsWith("/") ? workdir.slice(0, -1) : workdir;
  if (filePath === base) return ""; // the workdir itself — degenerate
  const prefix = base + "/";
  if (filePath.startsWith(prefix)) {
    return filePath.slice(prefix.length);
  }
  return null;
}
