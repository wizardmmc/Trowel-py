import { describe, it, expect } from "vitest";

import { splitBashCommand } from "../components/cc/bashCommand";

describe("splitBashCommand", () => {
  it("a single command → one segment with empty sep", () => {
    expect(splitBashCommand("ls -la")).toEqual([{ sep: "", body: "ls -la" }]);
  });

  it("splits on ';' ; the sep rides on each FOLLOWING segment", () => {
    expect(splitBashCommand("a; b; c")).toEqual([
      { sep: "", body: "a" },
      { sep: ";", body: "b" },
      { sep: ";", body: "c" },
    ]);
  });

  it("splits on '&&'", () => {
    expect(splitBashCommand("a && b")).toEqual([
      { sep: "", body: "a" },
      { sep: "&&", body: "b" },
    ]);
  });

  it("splits on '||'", () => {
    expect(splitBashCommand("a || b")).toEqual([
      { sep: "", body: "a" },
      { sep: "||", body: "b" },
    ]);
  });

  it("splits on '|'", () => {
    expect(splitBashCommand("a | b")).toEqual([
      { sep: "", body: "a" },
      { sep: "|", body: "b" },
    ]);
  });

  it("does NOT split on ';' inside double quotes", () => {
    expect(splitBashCommand('echo "a;b"; ls')).toEqual([
      { sep: "", body: 'echo "a;b"' },
      { sep: ";", body: "ls" },
    ]);
  });

  it("does NOT split on '|' inside double quotes", () => {
    expect(splitBashCommand('grep "a|b" f; ls')).toEqual([
      { sep: "", body: 'grep "a|b" f' },
      { sep: ";", body: "ls" },
    ]);
  });

  it("does NOT split on ';' inside single quotes", () => {
    expect(splitBashCommand("echo 'a;b'; ls")).toEqual([
      { sep: "", body: "echo 'a;b'" },
      { sep: ";", body: "ls" },
    ]);
  });

  it("empty string → []", () => {
    expect(splitBashCommand("")).toEqual([]);
  });

  it("whitespace-only → []", () => {
    expect(splitBashCommand("   ")).toEqual([]);
  });

  it("no spaces around ';' still splits", () => {
    expect(splitBashCommand("a;b")).toEqual([
      { sep: "", body: "a" },
      { sep: ";", body: "b" },
    ]);
  });

  it("trailing separator → no trailing empty segment", () => {
    expect(splitBashCommand("a;")).toEqual([{ sep: "", body: "a" }]);
  });

  it("consecutive separators → no empty segments", () => {
    expect(splitBashCommand("a;;b")).toEqual([
      { sep: "", body: "a" },
      { sep: ";", body: "b" },
    ]);
  });

  it("a leading separator is dropped (no empty first segment)", () => {
    expect(splitBashCommand(";a;b")).toEqual([
      { sep: "", body: "a" },
      { sep: ";", body: "b" },
    ]);
  });

  it("the real-world mockup command splits as expected", () => {
    const cmd =
      'cd /x; echo "===docs==="; ls docs/*.md 2>/dev/null; echo ""; ' +
      'grep -rEn "typecheck|mypy" pyproject.toml 2>/dev/null | head';
    const segs = splitBashCommand(cmd);
    expect(segs.length).toBe(6);
    expect(segs[0]).toEqual({ sep: "", body: "cd /x" });
    expect(segs[1]).toEqual({ sep: ";", body: 'echo "===docs==="' });
    expect(segs[2]).toEqual({
      sep: ";",
      body: "ls docs/*.md 2>/dev/null",
    });
    expect(segs[3]).toEqual({ sep: ";", body: 'echo ""' });
    expect(segs[4]).toEqual({
      sep: ";",
      body: 'grep -rEn "typecheck|mypy" pyproject.toml 2>/dev/null',
    });
    expect(segs[5]).toEqual({ sep: "|", body: "head" });
  });
});
