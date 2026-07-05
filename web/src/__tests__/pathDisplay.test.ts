import { describe, it, expect } from "vitest";
import { getDisplayPath } from "../components/cc/pathDisplay";

describe("getDisplayPath", () => {
  it("returns the absolute path when workdir is undefined", () => {
    expect(getDisplayPath("/a/b/c.ts")).toBe("/a/b/c.ts");
  });

  it("returns the absolute path when workdir is empty", () => {
    expect(getDisplayPath("/a/b/c.ts", "")).toBe("/a/b/c.ts");
  });

  it("returns project-relative when file is inside workdir", () => {
    expect(
      getDisplayPath("/Users/me/proj/web/src/x.ts", "/Users/me/proj"),
    ).toBe("web/src/x.ts");
  });

  it("returns relative when file is directly under workdir", () => {
    expect(getDisplayPath("/a/b/c.ts", "/a/b")).toBe("c.ts");
  });

  it("returns absolute when file is outside workdir (no ../)", () => {
    expect(getDisplayPath("/elsewhere/x.ts", "/a/b")).toBe("/elsewhere/x.ts");
  });

  it("does NOT mis-strip a workdir that is a string prefix but not a parent", () => {
    // workdir=/a/b, file=/a/beta/x.ts — "/a/b" is a prefix but "/a/b"+" "/""
    // must not match. The file is outside; expect absolute.
    expect(getDisplayPath("/a/beta/x.ts", "/a/b")).toBe("/a/beta/x.ts");
  });

  it("handles workdir with a trailing slash", () => {
    expect(getDisplayPath("/a/b/c.ts", "/a/b/")).toBe("c.ts");
  });
});
