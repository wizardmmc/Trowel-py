import { structuredPatch, type StructuredPatchHunk } from "diff";
import type { DiffHunk } from "../../api/ccTypes";

/** Result of diffing an Edit/MultiEdit's input or a Write-overwrite snapshot. */
export interface EditDiff {
  readonly hunks: readonly DiffHunk[];
  readonly add: number;
  readonly remove: number;
}

/** Tool_use input shape for Edit/MultiEdit (snake_case as it arrives from cc). */
export interface EditInput {
  readonly old_string?: unknown;
  readonly new_string?: unknown;
  /** MultiEdit: list of single edits, each with its own old/new_string. */
  readonly edits?: unknown;
}

/** Context lines around each change — matches CC `CONTEXT_LINES = 3`. */
const CONTEXT_LINES = 3;

function asString(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

function normalizeHunk(h: StructuredPatchHunk): DiffHunk {
  return {
    oldStart: h.oldStart,
    oldLines: h.oldLines,
    newStart: h.newStart,
    newLines: h.newLines,
    lines: h.lines,
  };
}

/** Count + and − lines across a list of hunks. */
export function summarizeStat(
  hunks: readonly DiffHunk[],
): { readonly add: number; readonly remove: number } {
  let add = 0;
  let remove = 0;
  for (const h of hunks) {
    for (const line of h.lines) {
      if (line.startsWith("+")) add += 1;
      else if (line.startsWith("-")) remove += 1;
    }
  }
  return { add, remove };
}

/** Patch one old→new fragment into hunks (3 lines context, jsdiff). Returns
 * empty array when inputs are missing or identical. */
function patchFragment(
  oldStr: string,
  newStr: string,
): readonly DiffHunk[] {
  if (oldStr === newStr) return [];
  const result = structuredPatch(
    "a",
    "b",
    oldStr,
    newStr,
    undefined,
    undefined,
    { context: CONTEXT_LINES },
  );
  return result.hunks.map(normalizeHunk);
}

/**
 * Compute a line-level diff for an Edit or MultiEdit tool_use from its input.
 *
 * Edit: diffs `old_string` against `new_string`. MultiEdit: concatenates the
 * per-edit fragment diffs (tcc has no full file content, so each edit becomes
 * its own hunk group — the same fallback CC uses in `diffToolInputsOnly`).
 *
 * Returns null when there is nothing to show (missing inputs, or every edit is
 * a no-op). Edit-create (`old_string === ""`) returns an all-additions diff,
 * matching CC's `Create` rendering.
 */
export function computeEditDiff(input: EditInput): EditDiff | null {
  const fragmentHunks: DiffHunk[] = [];

  if (Array.isArray(input.edits)) {
    for (const e of input.edits) {
      if (typeof e !== "object" || e === null) continue;
      const rec = e as Record<string, unknown>;
      const o = asString(rec.old_string);
      const n = asString(rec.new_string);
      if (o === null || n === null) continue;
      fragmentHunks.push(...patchFragment(o, n));
    }
  } else {
    const o = asString(input.old_string);
    const n = asString(input.new_string);
    if (o === null || n === null) return null;
    fragmentHunks.push(...patchFragment(o, n));
  }

  if (fragmentHunks.length === 0) return null;
  const stat = summarizeStat(fragmentHunks);
  return { hunks: fragmentHunks, ...stat };
}
