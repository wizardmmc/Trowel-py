import type { ToolItem } from "../../stores/ccStore";
import { getDisplayPath } from "./pathDisplay";

export type CodexCommandVerb = "Read" | "List" | "Search" | "Run";

export interface CodexCommandRow {
  readonly verb: CodexCommandVerb;
  readonly detail: string;
}

export interface CodexCommandPresentation {
  readonly kind: "exploration" | "run";
  readonly rows: readonly CodexCommandRow[];
  readonly fullCommand: string;
  readonly callLabel: string;
  readonly callBrief: string;
}

interface NativeAction {
  readonly type: "read" | "listFiles" | "search" | "unknown";
  readonly command: string | null;
  readonly name?: string | null;
  readonly path?: string | null;
  readonly query?: string | null;
}

const EXPLORATION_ACTIONS = new Set<NativeAction["type"]>([
  "read",
  "listFiles",
  "search",
]);

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function parseActions(input: Record<string, unknown>): readonly NativeAction[] {
  const raw = input.command_actions;
  if (!Array.isArray(raw)) return [];
  const actions: NativeAction[] = [];
  for (const value of raw) {
    if (typeof value !== "object" || value === null) return [];
    const action = value as Record<string, unknown>;
    const type = action.type;
    if (type !== "read" && type !== "listFiles" && type !== "search" && type !== "unknown") {
      return [];
    }
    actions.push({
      type,
      command: stringOrNull(action.command),
      name: stringOrNull(action.name),
      path: stringOrNull(action.path),
      query: stringOrNull(action.query),
    });
  }
  return actions;
}

function commandPreview(command: string): string {
  const matched = /^(?:\/bin\/)?(?:zsh|bash|sh)\s+-lc\s+([\s\S]+)$/.exec(command.trim());
  if (matched === null) return command.trim();
  const inner = matched[1].trim();
  if (inner.length >= 2) {
    const first = inner[0];
    const last = inner[inner.length - 1];
    if ((first === "'" && last === "'") || (first === '"' && last === '"')) {
      return inner.slice(1, -1);
    }
  }
  return inner;
}

function fallback(action: NativeAction, fullCommand: string): string {
  return action.command ?? commandPreview(fullCommand);
}

function readRange(command: string | null): string | null {
  if (command === null) return null;
  const matched = /\bsed\s+-n\s+['"]?(\d+),(\d+)p['"]?/.exec(command);
  return matched === null ? null : `lines ${matched[1]}–${matched[2]}`;
}

function explorationRow(
  action: NativeAction,
  fullCommand: string,
  workdir?: string,
): CodexCommandRow {
  if (action.type === "read") {
    const base = action.path
      ? getDisplayPath(action.path, workdir)
      : action.name ?? fallback(action, fullCommand);
    const range = readRange(action.command);
    return {
      verb: "Read",
      detail: range === null ? base : `${base} · ${range}`,
    };
  }
  if (action.type === "listFiles") {
    return { verb: "List", detail: action.path ?? "." };
  }
  return {
    verb: "Search",
    detail:
      action.query && action.path
        ? `${action.query} in ${action.path}`
        : fallback(action, fullCommand),
  };
}

export function getCodexCommandPresentation(
  item: ToolItem,
  workdir?: string,
): CodexCommandPresentation {
  const fullCommand = stringOrNull(item.input.command) ?? "";
  const actions = parseActions(item.input);
  const exploring =
    item.toolName === "command" &&
    actions.length > 0 &&
    actions.every((action) => EXPLORATION_ACTIONS.has(action.type));
  if (!exploring) {
    const unknown = actions.find((action) => action.type === "unknown");
    return {
      kind: "run",
      rows: [{ verb: "Run", detail: unknown?.command ?? commandPreview(fullCommand) }],
      fullCommand,
      callLabel: item.status === "failed" ? "Failed" : item.status === "running" ? "Running" : "Ran",
      callBrief: unknown?.command ?? commandPreview(fullCommand),
    };
  }
  const rows = actions.map((action) =>
    explorationRow(action, fullCommand, workdir),
  );
  const counts = new Map<CodexCommandVerb, number>();
  for (const row of rows) {
    counts.set(row.verb, (counts.get(row.verb) ?? 0) + 1);
  }
  const onlyVerb = counts.size === 1 ? rows[0].verb : null;
  const noun =
    onlyVerb === "Read" ? "file" : onlyVerb === "List" ? "path" : "query";
  const callLabel =
    onlyVerb === null
      ? `Explore ${rows.length} ${rows.length === 1 ? "action" : "actions"}`
      : `${onlyVerb} ${rows.length} ${noun}${rows.length === 1 ? "" : "s"}`;
  const callBrief =
    onlyVerb === null
      ? (["Read", "Search", "List"] as const)
          .flatMap((verb) => {
            const count = counts.get(verb) ?? 0;
            return count > 0 ? [`${count} ${verb}`] : [];
          })
          .join(" · ")
      : rows.map((row) => row.detail).join(", ");
  return {
    kind: "exploration",
    rows,
    fullCommand,
    callLabel,
    callBrief,
  };
}

export function isCodexExploration(item: ToolItem): boolean {
  return getCodexCommandPresentation(item).kind === "exploration";
}
