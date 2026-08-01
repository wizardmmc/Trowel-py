import type { ToolItem } from "../agent/domain";

export function tool(over: Partial<ToolItem> = {}): ToolItem {
  return {
    kind: "tool",
    toolUseId: "t1",
    toolName: "Write",
    input: { file_path: "/a/b.txt", content: "x" },
    status: "running",
    elapsedSeconds: null,
    result: null,
    childTools: [],
    ...over,
  };
}

export const CATN_3 =
  "     1\t\n     2\t\n     3\tfrom fastapi import FastAPI\n";
