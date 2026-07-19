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

/** Remove only the app-server's outer shell wrapper for a compact preview.
 * This never decides action semantics; Read/List/Search still come exclusively
 * from command_actions. */
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

function explorationRow(
  action: NativeAction,
  fullCommand: string,
  workdir?: string,
): CodexCommandRow {
  if (action.type === "read") {
    return {
      verb: "Read",
      detail: action.path ? getDisplayPath(action.path, workdir) : action.name ?? fallback(action, fullCommand),
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

/** Present a Codex command using only its native commandActions semantics. */
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
    };
  }
  return {
    kind: "exploration",
    rows: actions.map((action) => explorationRow(action, fullCommand, workdir)),
    fullCommand,
  };
}

export function isCodexExploration(item: ToolItem): boolean {
  return getCodexCommandPresentation(item).kind === "exploration";
}
