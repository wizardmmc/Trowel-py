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

  it("uses the native List command when Codex only supplies a basename", () => {
    const item = command({
      command: "/bin/zsh -lc 'rg --files docs/foundation'",
      cwd: "/repo",
      command_actions: [
        {
          type: "listFiles",
          command: "rg --files docs/foundation",
          path: "foundation",
        },
      ],
    });

    expect(getCodexCommandPresentation(item, "/repo").rows[0]).toEqual({
      verb: "List",
      detail: "rg --files docs/foundation",
    });
  });

  it.each([
    [
      "read",
      {
        type: "read",
        command: "cat docs/slices/activate/README.md",
        path: "README.md",
      },
      "Read",
      "cat docs/slices/activate/README.md",
    ],
    [
      "search",
      {
        type: "search",
        command: "rg Explore docs/slices/activate/README.md",
        query: "Explore",
        path: "README.md",
      },
      "Search",
      "rg Explore docs/slices/activate/README.md",
    ],
  ])(
    "uses the native %s command when Codex only supplies a basename",
    (_type, action, verb, detail) => {
      const item = command({
        command: "inspect",
        command_actions: [action],
      });

      expect(getCodexCommandPresentation(item, "/repo").rows[0]).toEqual({
        verb,
        detail,
      });
    },
  );

  it("normalizes List and Search paths against the active workdir", () => {
    const item = command({
      command: "inspect",
      command_actions: [
        {
          type: "listFiles",
          command: "find /repo/docs/foundation",
          path: "/repo/docs/foundation",
        },
        {
          type: "search",
          command: "rg TODO /repo/web/src",
          query: "TODO",
          path: "/repo/web/src",
        },
        {
          type: "search",
          command: "rg TODO /opt/shared",
          query: "TODO",
          path: "/opt/shared",
        },
      ],
    });

    expect(getCodexCommandPresentation(item, "/repo").rows).toEqual([
      { verb: "List", detail: "docs/foundation" },
      { verb: "Search", detail: "TODO in web/src" },
      { verb: "Search", detail: "TODO in /opt/shared" },
    ]);
  });
});
