/**
 * slice-042 P5: render the picker against a SYNTHETIC scale roster (real-shape,
 * no PII) — 200+ plugin skills + ~80 bundled + mcp__/TUI noise. This is the
 * scale case that motivated the plugin-collapse feature: 200+ plugin skills
 * must not drown the list, and the picker must group/collapse/search them
 * without choking.
 *
 * Fixture: ./fixtures/cc-init-291.json holds a synthetic slash_commands roster
 * (the real recording carried machine paths / plugin inventory and was replaced
 * — see slice-093-pre P1). Loaded via vite's `?raw` so no node types are needed
 * (the app tsconfig deliberately keeps node types out of src/). We mirror the
 * backend's init-floor rules (filter mcp__ + TUI commands; ":" in name →
 * plugin, else bundled) to build SlashItems. Source classification for
 * disk-present skills is the backend's job and tested there — here we only
 * prove the frontend handles the roster's shape and volume.
 */
import { describe, it, expect } from "vitest";
import rosterRaw from "./fixtures/cc-init-291.json?raw";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { SlashAutocomplete } from "../components/cc/SlashAutocomplete";
import { groupSlashItems, type SlashSource } from "../components/cc/slashGroups";
import type { SlashItem } from "../api/cc";

// cc's TUI / debug commands the backend filters out (slash_items._CC_TUI_COMMANDS).
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

describe("SlashAutocomplete × real cc roster (slice-042 P5)", () => {
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
    // every filtered roster item renders (assertion is relative to items.length,
    // so it holds for the synthetic roster regardless of exact count).
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
    expect(pluginGroup?.items.length).toBeGreaterThan(150); // synthetic: 200+ plugins
    // header present, body hidden → only non-plugin options render
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
    expect(matched.length).toBeLessThan(items.length); // narrowed
    // every rendered item's NAME (not the badge/desc text sharing the option)
    // contains the query substring — checks the name element directly so a
    // query that happens to match a source badge label can't false-pass.
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
