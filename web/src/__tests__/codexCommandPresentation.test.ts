import { describe, expect, it } from "vitest";

import {
  getCodexCommandPresentation,
  isCodexExploration,
} from "../components/cc/codexCommandPresentation";
import type { ToolItem } from "../stores/ccStore";

function command(input: Record<string, unknown>): ToolItem {
  return {
    kind: "tool",
    toolUseId: "c1",
    toolName: "command",
    input,
    status: "done",
    elapsedSeconds: null,
    result: null,
    childTools: [],
  };
}

describe("Codex command presentation — native commandActions only", () => {
  it.each([
    [
      "read",
      { type: "read", command: "sed alpha", name: "alpha.txt", path: "/repo/alpha.txt" },
      "Read",
      "alpha.txt",
    ],
    ["listFiles", { type: "listFiles", command: "ls", path: null }, "List", "."],
    [
      "search",
      { type: "search", command: "rg TODO .", query: "TODO", path: "." },
      "Search",
      "TODO in .",
    ],
  ])("maps %s without guessing from the shell", (_type, action, verb, detail) => {
    const item = command({
      command: "/bin/zsh -lc 'anything at all'",
      command_actions: [action],
    });
    const presented = getCodexCommandPresentation(item, "/repo");
    expect(presented.kind).toBe("exploration");
    expect(presented.rows).toEqual([{ verb, detail }]);
    expect(isCodexExploration(item)).toBe(true);
  });

  it.each([
    ["unknown", [{ type: "unknown", command: "npm test" }]],
    [
      "mixed",
      [
        { type: "read", command: "cat a", name: "a", path: "/repo/a" },
        { type: "unknown", command: "npm test" },
      ],
    ],
    ["missing", []],
  ])("renders %s actions as Run", (_case, actions) => {
    const item = command({ command: "/bin/zsh -lc 'npm test'", command_actions: actions });
    const presented = getCodexCommandPresentation(item, "/repo");
    expect(presented.kind).toBe("run");
    expect(presented.rows[0]).toEqual({ verb: "Run", detail: "npm test" });
    expect(isCodexExploration(item)).toBe(false);
  });

  it("renders every action from one native exploration command", () => {
    const item = command({
      command: "inspect",
      command_actions: [
        { type: "read", command: "cat a", name: "a", path: "/repo/a" },
        { type: "search", command: "rg x", query: "x", path: "src" },
      ],
    });
    expect(getCodexCommandPresentation(item, "/repo").rows).toEqual([
      { verb: "Read", detail: "a" },
      { verb: "Search", detail: "x in src" },
    ]);
  });

  it("adds native sed ranges so same-path reads remain distinguishable", () => {
    const first = command({
      command: "/bin/zsh -lc sed",
      command_actions: [
        {
          type: "read",
          command: "sed -n '1,320p' /repo/SKILL.md",
          path: "/repo/SKILL.md",
        },
      ],
    });
    const second = command({
      command: "/bin/zsh -lc sed",
      command_actions: [
        {
          type: "read",
          command: "sed -n '321,700p' /repo/SKILL.md",
          path: "/repo/SKILL.md",
        },
      ],
    });
    expect(getCodexCommandPresentation(first, "/repo").rows[0].detail).toBe(
      "SKILL.md · lines 1–320",
    );
    expect(getCodexCommandPresentation(second, "/repo").rows[0].detail).toBe(
      "SKILL.md · lines 321–700",
    );
  });
});
