import type { ToolItem } from "../../stores/ccStore";
import { computeEditDiff, summarizeStat } from "./editDiff";

export function isEditTool(name: string): boolean {
  return name === "Edit" || name === "MultiEdit";
}

export function isDiffTool(name: string): boolean {
  return isEditTool(name) || name === "Write" || name === "apply_patch";
}

const APPLY_PATCH_VERB: Readonly<Record<string, string>> = {
  add: "Create",
  modify: "Update",
  delete: "Delete",
  rename: "Rename",
};

export function asString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

export function displayVerb(item: ToolItem): string {
  if (isEditTool(item.toolName)) {
    const oldString = item.input.old_string;
    return typeof oldString === "string" && oldString === ""
      ? "Create"
      : "Update";
  }
  if (item.toolName === "Write") return "Write";
  if (item.toolName === "apply_patch") {
    const kinds = item.input.change_kinds;
    const first =
      Array.isArray(kinds) && typeof kinds[0] === "string" ? kinds[0] : "";
    return APPLY_PATCH_VERB[first] ?? item.toolName;
  }
  if (item.toolName === "command") {
    if (item.status === "failed") return "Failed";
    return item.status === "running" ? "Running" : "Ran";
  }
  return item.toolName;
}

/** 末尾换行只作为终止符，不额外计一行。 */
export function countLines(text: string): number {
  if (text === "") return 0;
  const parts = text.split("\n");
  return text.endsWith("\n") ? parts.length - 1 : parts.length;
}

export function summaryStat(
  item: ToolItem,
): { add: number; remove: number } | null {
  if (item.status !== "done") return null;
  if (isEditTool(item.toolName)) {
    const writeDiff = item.writeDiff;
    if (writeDiff?.type === "update") return summarizeStat(writeDiff.hunks);
    const diff = computeEditDiff(item.input);
    return diff === null ? null : { add: diff.add, remove: diff.remove };
  }
  if (item.toolName === "Write") {
    const writeDiff = item.writeDiff;
    if (writeDiff?.type === "update") return summarizeStat(writeDiff.hunks);
    const content = asString(item.input.content);
    return content === null
      ? null
      : { add: countLines(content), remove: 0 };
  }
  if (item.toolName === "apply_patch") {
    const writeDiff = item.writeDiff;
    return writeDiff && writeDiff.hunks.length > 0
      ? summarizeStat(writeDiff.hunks)
      : null;
  }
  return null;
}

export function summaryLines(item: ToolItem): number | null {
  if (
    item.toolName !== "Read" ||
    item.status !== "done" ||
    item.result === null
  ) {
    return null;
  }
  return countLines(item.result);
}

export function brief(text: string, max = 48): string {
  const oneLine = text.replace(/\s+/g, " ").trim();
  return oneLine.length > max
    ? `${oneLine.slice(0, max - 1)}…`
    : oneLine;
}

export function statSentence(add: number, remove: number): string {
  const added =
    add > 0 ? `Added ${add} ${add === 1 ? "line" : "lines"}` : "";
  const removed =
    remove > 0
      ? `${add === 0 ? "R" : "r"}emoved ${remove} ${remove === 1 ? "line" : "lines"}`
      : "";
  return [added, removed].filter(Boolean).join(", ");
}

/** 解析 CC `cat -n` 输出；任一行不匹配时由调用方回退到纯文本。 */
export function parseCatN(
  text: string,
): readonly { readonly num: string; readonly content: string }[] | null {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  if (lines.length > 0 && lines[lines.length - 1] === "") lines.pop();
  if (lines.length === 0) return null;

  const rows: { num: string; content: string }[] = [];
  for (const line of lines) {
    const match = /^(\s*)(\d+)\t(.*)$/.exec(line);
    if (match === null) return null;
    rows.push({ num: match[2], content: match[3] });
  }
  return rows;
}
