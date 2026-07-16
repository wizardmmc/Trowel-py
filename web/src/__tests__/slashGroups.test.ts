import { describe, it, expect } from "vitest";
import type { SlashItem } from "../api/cc";
import {
  groupSlashItems,
  isGroupExpanded,
  flatVisible,
  SLASH_SOURCE_ORDER,
  type SlashSource,
} from "../components/cc/slashGroups";

// Real-shaped names drawn from the cc-init-291 fixture (cc 2.1.197): one item
// per source so every grouping branch is exercised. Plugin names carry the
// "mp:skill" full name the backend emits (C-4).
const items: readonly SlashItem[] = [
  { name: "model", description: "切换模型", source: "builtin", type: "command" },
  { name: "deep-research", description: "多源深研", source: "bundled", type: "skill" },
  { name: "grill-me", description: "拷问 spec", source: "user", type: "skill" },
  { name: "deploy", description: "部署", source: "project", type: "command" },
  {
    name: "everything-claude-code:code-review",
    description: "Expert code review",
    source: "plugin",
    type: "skill",
  },
  {
    name: "codex:rescue",
    description: "Codex rescue",
    source: "plugin",
    type: "command",
  },
];

describe("groupSlashItems (slice-042 P4)", () => {
  it("groups by source in SLASH_SOURCE_ORDER and drops empty groups", () => {
    const groups = groupSlashItems(items, "");
    expect(groups.map((g) => g.source)).toEqual([
      "builtin",
      "bundled",
      "user",
      "project",
      "plugin",
    ]);
  });

  it("source order is builtin → bundled → user → project → plugin", () => {
    expect(SLASH_SOURCE_ORDER).toEqual([
      "builtin",
      "bundled",
      "user",
      "project",
      "plugin",
    ]);
  });

  it("sorts items within a group by name (case-insensitive)", () => {
    const groups = groupSlashItems(items, "");
    const plugin = groups.find((g) => g.source === "plugin");
    expect(plugin?.items.map((i) => i.name)).toEqual([
      "codex:rescue",
      "everything-claude-code:code-review",
    ]);
  });

  it("filters by name substring across all groups (case-insensitive)", () => {
    const groups = groupSlashItems(items, "code");
    // "code" matches inside both plugin names: "codex" contains "code", and
    // "...code-review" too. No other source's name contains "code", so only the
    // plugin bucket survives — demonstrating substring (not prefix) matching.
    const sources = groups.map((g) => g.source);
    expect(sources).toEqual(["plugin"]);
    const plugin = groups.find((g) => g.source === "plugin");
    expect(plugin?.items.map((i) => i.name)).toEqual([
      "codex:rescue",
      "everything-claude-code:code-review",
    ]);
  });

  it("empty query returns all items (no filtering)", () => {
    const all = groupSlashItems(items, "").flatMap((g) => g.items);
    expect(all).toHaveLength(items.length);
  });

  it("query is trimmed before matching", () => {
    const groups = groupSlashItems(items, "  grill  ");
    expect(groups.flatMap((g) => g.items).map((i) => i.name)).toEqual(["grill-me"]);
  });

  it("no matches → no groups", () => {
    expect(groupSlashItems(items, "zzzzz")).toEqual([]);
  });
});

describe("isGroupExpanded (slice-042 P4)", () => {
  const collapsed = new Set<SlashSource>(["plugin"]);

  it("searching expands every group (matches-only are present anyway)", () => {
    expect(isGroupExpanded("plugin", true, collapsed)).toBe(true);
    expect(isGroupExpanded("user", true, collapsed)).toBe(true);
  });

  it("when not searching, the collapsed set controls", () => {
    expect(isGroupExpanded("plugin", false, collapsed)).toBe(false);
    expect(isGroupExpanded("user", false, collapsed)).toBe(true);
  });

  it("empty collapsed set → everything expanded when not searching", () => {
    expect(isGroupExpanded("plugin", false, new Set())).toBe(true);
  });
});

describe("flatVisible (slice-042 P4)", () => {
  it("flattens only expanded groups, in group order — matches render order", () => {
    const groups = groupSlashItems(items, "");
    // plugin collapsed, rest expanded → plugin's 2 items dropped from the flat
    // keyboard list. This is the order SlashAutocomplete renders expanded rows.
    const flat = flatVisible(groups, false, new Set(["plugin"]));
    expect(flat.map((i) => i.name)).toEqual([
      "model",
      "deep-research",
      "grill-me",
      "deploy",
    ]);
  });

  it("searching → all groups expanded → every match in the flat list", () => {
    const groups = groupSlashItems(items, "code");
    const flat = flatVisible(groups, true, new Set(["plugin"]));
    expect(flat.map((i) => i.name)).toEqual([
      "codex:rescue",
      "everything-claude-code:code-review",
    ]);
  });

  it("aligns with groupSlashItems order (no reordering)", () => {
    const groups = groupSlashItems(items, "");
    const flat = flatVisible(groups, false, new Set());
    // same as walking every group's items in order
    const expected = groups.flatMap((g) => g.items).map((i) => i.name);
    expect(flat.map((i) => i.name)).toEqual(expected);
  });
});
