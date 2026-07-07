/**
 * Split a bash command line into one segment per top-level statement.
 *
 * slice-033 feat 4 (方案 A): a long `cd ...; echo ...; ls ...; ... | head`
 * command renders as one ugly wrapped line. We break it at top-level
 * separators (`;`, `&&`, `||`, `|`) so each statement gets its own line.
 *
 * Quote-aware: separators inside single/double quotes are NOT split points
 * (e.g. `echo "a;b"` stays one segment). This is best-effort shell tokenizing,
 * NOT a full shell parse — it does not understand `$()`, backticks, `${}`, or
 * here-docs; separators nested in those are treated as top-level. That matches
 * CC's own display behavior (CC doesn't split either) and is documented in the
 * slice spec as an accepted edge.
 */

export interface BashSegment {
  /** The separator that preceded this segment (";", "&&", "||", "|"), or "" for the first. */
  readonly sep: "" | ";" | "&&" | "||" | "|";
  /** The command text, whitespace-trimmed, with the separator stripped. */
  readonly body: string;
}

/** Quote char currently open (null when not inside quotes). */
type Quote = '"' | "'" | null;

/**
 * Split `cmd` into segments at top-level `;`, `&&`, `||`, `|`.
 *
 * Args:
 *   cmd: the raw bash command string from a Bash tool_use input.
 *
 * Returns:
 *   one `BashSegment` per top-level statement (the separator rides on the
 *   FOLLOWING segment, so the first segment always has `sep === ""`). Empty
 *   for an empty/whitespace input. Consecutive or trailing separators produce
 *   no empty segments.
 */
export function splitBashCommand(cmd: string): readonly BashSegment[] {
  const segments: BashSegment[] = [];
  let quote: Quote = null;
  let sep: BashSegment["sep"] = "";
  let start = 0;

  for (let i = 0; i < cmd.length; ) {
    const ch = cmd[i] as string | undefined;
    if (ch === undefined) break;

    // Inside quotes: only watch for the closing quote.
    if (quote !== null) {
      if (ch === quote) quote = null;
      i += 1;
      continue;
    }

    // Opening a quote.
    if (ch === '"' || ch === "'") {
      quote = ch;
      i += 1;
      continue;
    }

    // Top-level separator?
    let nextSep: BashSegment["sep"] | null = null;
    if (ch === ";") {
      nextSep = ";";
    } else if (ch === "&" && cmd[i + 1] === "&") {
      nextSep = "&&";
    } else if (ch === "|" && cmd[i + 1] === "|") {
      nextSep = "||";
    } else if (ch === "|") {
      nextSep = "|";
    }

    if (nextSep === null) {
      i += 1;
      continue;
    }

    // Flush the segment before this separator. An empty body (consecutive
    // separators, or a leading separator) is dropped — and sep is left
    // unchanged, so a leading separator doesn't poison the first real segment
    // and a run of separators collapses cleanly.
    const body = cmd.slice(start, i).trim();
    if (body !== "") {
      segments.push({ sep, body });
      sep = nextSep;
    }

    const step = nextSep === "&&" || nextSep === "||" ? 2 : 1;
    start = i + step;
    i += step;
  }

  // Trailing segment.
  const body = cmd.slice(start).trim();
  if (body !== "") segments.push({ sep, body });
  return segments;
}
