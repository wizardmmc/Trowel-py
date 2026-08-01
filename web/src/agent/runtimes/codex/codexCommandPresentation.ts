/** 把 Codex 命令动作转换为时间线使用的操作摘要。 */

import type { ToolItem } from "../../domain";
import { getDisplayPath } from "../shared";

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

interface ActionPathDetail {
  readonly text: string;
  readonly fromCommand: boolean;
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

function displayActionPath(path: string, workdir?: string): string {
  return getDisplayPath(path, workdir) || ".";
}

function actionPathDetail(
  action: NativeAction,
  workdir?: string,
): ActionPathDetail | null {
  if (action.path === null || action.path === undefined) return null;
  const weakBasename =
    action.path !== "." &&
    !action.path.startsWith("/") &&
    !action.path.includes("/");
  if (weakBasename && action.command?.includes("/")) {
    return { text: action.command, fromCommand: true };
  }
  return {
    text: displayActionPath(action.path, workdir),
    fromCommand: false,
  };
}

function explorationRow(
  action: NativeAction,
  fullCommand: string,
  workdir?: string,
): CodexCommandRow {
  if (action.type === "read") {
    const pathDetail = actionPathDetail(action, workdir);
    const base =
      pathDetail?.text ??
      action.name ??
      fallback(action, fullCommand);
    const range = readRange(action.command);
    return {
      verb: "Read",
      detail: range === null ? base : `${base} · ${range}`,
    };
  }
  if (action.type === "listFiles") {
    return {
      verb: "List",
      detail: actionPathDetail(action, workdir)?.text ?? ".",
    };
  }
  const searchPath = actionPathDetail(action, workdir);
  const searchDetail =
    searchPath?.fromCommand
      ? searchPath.text
      : action.query && searchPath
        ? `${action.query} in ${searchPath.text}`
        : fallback(action, fullCommand);
  return {
    verb: "Search",
    detail: searchDetail,
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
    };
  }
  const rows = actions.map((action) =>
    explorationRow(action, fullCommand, workdir),
  );
  return {
    kind: "exploration",
    rows,
    fullCommand,
  };
}

export function isCodexExploration(item: ToolItem): boolean {
  return getCodexCommandPresentation(item).kind === "exploration";
}
