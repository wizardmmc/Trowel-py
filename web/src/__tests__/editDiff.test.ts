import { describe, it, expect } from "vitest";
import { computeEditDiff, summarizeStat } from "../components/cc/editDiff";

describe("computeEditDiff", () => {
  it("returns null when old/new are identical", () => {
    expect(
      computeEditDiff({ old_string: "same", new_string: "same" }),
    ).toBeNull();
  });

  it("returns null when inputs are missing", () => {
    expect(computeEditDiff({})).toBeNull();
    expect(computeEditDiff({ old_string: "a" })).toBeNull();
    expect(computeEditDiff({ new_string: "a" })).toBeNull();
  });

  it("returns null when inputs are non-string", () => {
    expect(
      computeEditDiff({ old_string: 42, new_string: "a" }),
    ).toBeNull();
  });

  it("counts a pure addition", () => {
    const d = computeEditDiff({
      old_string: "line a\nline b\n",
      new_string: "line a\nline b\nline c\n",
    });
    expect(d).not.toBeNull();
    expect(d!.add).toBe(1);
    expect(d!.remove).toBe(0);
    expect(d!.hunks.length).toBeGreaterThanOrEqual(1);
  });

  it("counts a pure removal", () => {
    const d = computeEditDiff({
      old_string: "line a\nline b\nline c\n",
      new_string: "line a\nline b\n",
    });
    expect(d!.add).toBe(0);
    expect(d!.remove).toBe(1);
  });

  it("counts a mixed change", () => {
    const d = computeEditDiff({
      old_string: "alpha\nbeta\ngamma",
      new_string: "alpha\nBETA\ngamma",
    });
    expect(d!.add).toBe(1);
    expect(d!.remove).toBe(1);
  });

  it("produces multiple hunks when changes are far apart", () => {
    const head = Array.from({ length: 8 }, (_, i) => `l${i}`).join("\n");
    const oldStr = `${head}\nREMOVE_ME\n${head}\nALSO_REMOVE\n`;
    const newStr = `${head}\n${head}\n`;
    const d = computeEditDiff({ old_string: oldStr, new_string: newStr });
    expect(d!.hunks.length).toBeGreaterThanOrEqual(2);
    expect(d!.remove).toBe(2);
  });

  it("treats empty old_string as all-additions (Edit-create)", () => {
    const d = computeEditDiff({
      old_string: "",
      new_string: "fresh\ncontent\nhere",
    });
    expect(d!.add).toBe(3);
    expect(d!.remove).toBe(0);
  });

  it("hunk lines carry the leading marker char (+/−/space)", () => {
    const d = computeEditDiff({
      old_string: "keep\nold",
      new_string: "keep\nnew",
    });
    expect(d).not.toBeNull();
    const allLines = d!.hunks.flatMap((h) => h.lines);
    expect(allLines.some((l) => l.startsWith("+"))).toBe(true);
    expect(allLines.some((l) => l.startsWith("-"))).toBe(true);
    expect(allLines.some((l) => l.startsWith(" "))).toBe(true);
  });

  it("aggregates multiple edits for MultiEdit (concatenated hunks)", () => {
    const d = computeEditDiff({
      edits: [
        { old_string: "a", new_string: "b" },
        { old_string: "x", new_string: "y" },
      ],
    });
    expect(d).not.toBeNull();
    expect(d!.add).toBe(2);
    expect(d!.remove).toBe(2);
  });

  it("skips identical edits inside a MultiEdit", () => {
    const d = computeEditDiff({
      edits: [
        { old_string: "same", new_string: "same" },
        { old_string: "a", new_string: "b" },
      ],
    });
    expect(d!.add).toBe(1);
    expect(d!.remove).toBe(1);
  });

  it("returns null for a MultiEdit where every edit is identical", () => {
    expect(
      computeEditDiff({
        edits: [{ old_string: "x", new_string: "x" }],
      }),
    ).toBeNull();
  });
});

describe("summarizeStat", () => {
  it("counts + and − lines across hunks", () => {
    const stat = summarizeStat([
      {
        oldStart: 1, oldLines: 2, newStart: 1, newLines: 3,
        lines: [" ctx", "+add1", "+add2", "-rm"],
      },
      {
        oldStart: 10, oldLines: 1, newStart: 11, newLines: 1,
        lines: [" ctx", "+add3"],
      },
    ]);
    expect(stat).toEqual({ add: 3, remove: 1 });
  });

  it("returns zeros for an empty hunk list", () => {
    expect(summarizeStat([])).toEqual({ add: 0, remove: 0 });
  });
});
