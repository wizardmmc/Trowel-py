import { structuredPatch, type StructuredPatchHunk } from "diff";
import type { DiffHunk } from "../../api/ccTypes";

export interface EditDiff {
  readonly hunks: readonly DiffHunk[];
  readonly add: number;
  readonly remove: number;
}

export interface EditInput {
  readonly old_string?: unknown;
  readonly new_string?: unknown;
  readonly edits?: unknown;
}

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
