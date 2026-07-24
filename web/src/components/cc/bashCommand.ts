
export interface BashSegment {
  readonly sep: "" | ";" | "&&" | "||" | "|";
  readonly body: string;
}

type Quote = '"' | "'" | null;

export function splitBashCommand(cmd: string): readonly BashSegment[] {
  const segments: BashSegment[] = [];
  let quote: Quote = null;
  let sep: BashSegment["sep"] = "";
  let start = 0;

  for (let i = 0; i < cmd.length; ) {
    const ch = cmd[i] as string | undefined;
    if (ch === undefined) break;

    if (quote !== null) {
      if (ch === quote) quote = null;
      i += 1;
      continue;
    }

    if (ch === '"' || ch === "'") {
      quote = ch;
      i += 1;
      continue;
    }

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

    const body = cmd.slice(start, i).trim();
    if (body !== "") {
      segments.push({ sep, body });
      sep = nextSep;
    }

    const step = nextSep === "&&" || nextSep === "||" ? 2 : 1;
    start = i + step;
    i += step;
  }

  const body = cmd.slice(start).trim();
  if (body !== "") segments.push({ sep, body });
  return segments;
}
