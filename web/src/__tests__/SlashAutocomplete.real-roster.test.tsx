import { describe, it, expect } from "vitest";
import rosterRaw from "./fixtures/cc-init-291.json?raw";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { SlashAutocomplete } from "../components/cc/SlashAutocomplete";
import { groupSlashItems, type SlashSource } from "../components/cc/slashGroups";
import type { SlashItem } from "../api/cc";

const CC_TUI_COMMANDS = new Set([
  "clear", "compact", "config", "context", "heapdump",
  "reload-skills", "usage", "insights", "goal",
]);

function buildRealItems(): SlashItem[] {
  const ev = JSON.parse(rosterRaw) as { slash_commands?: string[] };
  const roster = ev.slash_commands ?? [];
  return roster
    .filter((n) => !n.startsWith("mcp__") && !CC_TUI_COMMANDS.has(n))
    .map((n) => ({
      name: n,
      description: "",
      source: (n.includes(":") ? "plugin" : "bundled") as SlashSource,
      type: "skill" as const,
    }));
}

describe("SlashAutocomplete × real cc roster", () => {
  const items = buildRealItems();
  const noCollapse = new Set<SlashSource>();

  it("renders every filtered roster item when the plugin group is expanded", () => {
    const groups = groupSlashItems(items, "");
    render(
      <SlashAutocomplete
        groups={groups}
        searching={false}
        collapsed={noCollapse}
        selectedIndex={0}
        onSelect={() => {}}
        onToggleGroup={() => {}}
      />,
    );
    expect(screen.getAllByRole("option")).toHaveLength(items.length);
    cleanup();
  });

  it("collapses the plugin group by default — 200+ plugin rows hidden, header shown", () => {
    const groups = groupSlashItems(items, "");
    const collapsed = new Set<SlashSource>(["plugin"]);
    render(
      <SlashAutocomplete
        groups={groups}
        searching={false}
        collapsed={collapsed}
        selectedIndex={0}
        onSelect={() => {}}
        onToggleGroup={() => {}}
      />,
    );
    const pluginGroup = groups.find((g) => g.source === "plugin");
    expect(pluginGroup?.items.length).toBeGreaterThan(150);
    expect(screen.getByRole("button", { name: /plugin 组/ })).toBeInTheDocument();
    expect(screen.getAllByRole("option")).toHaveLength(
      items.length - (pluginGroup?.items.length ?? 0),
    );
    cleanup();
  });

  it("a substring search narrows the list and expands the plugin group", () => {
    const q = "review";
    const groups = groupSlashItems(items, q);
    render(
      <SlashAutocomplete
        groups={groups}
        searching={true}
        collapsed={new Set(["plugin"])}
        selectedIndex={0}
        onSelect={() => {}}
        onToggleGroup={() => {}}
      />,
    );
    const matched = screen.getAllByRole("option");
    expect(matched.length).toBeGreaterThan(0);
    expect(matched.length).toBeLessThan(items.length);
    for (const opt of matched) {
      const name = opt.querySelector(".cc-ac__name")?.textContent?.toLowerCase();
      expect(name).toContain(q);
    }
    cleanup();
  });

  it("plugin header click fires onToggleGroup (collapse toggle wires up at scale)", () => {
    let toggled: SlashSource | null = null;
    const groups = groupSlashItems(items, "");
    render(
      <SlashAutocomplete
        groups={groups}
        searching={false}
        collapsed={noCollapse}
        selectedIndex={0}
        onSelect={() => {}}
        onToggleGroup={(s) => {
          toggled = s;
        }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /plugin 组/ }));
    expect(toggled).toBe("plugin");
    cleanup();
  });
});
