import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SlashAutocomplete } from "../components/cc/SlashAutocomplete";
import { groupSlashItems, type SlashSource } from "../components/cc/slashGroups";
import type { SlashItem } from "../api/cc";

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
];

const noCollapse = new Set<SlashSource>();

function renderPicker(
  query: string,
  opts: {
    collapsed?: ReadonlySet<SlashSource>;
    selectedIndex?: number;
    onSelect?: (i: SlashItem) => void;
    onToggleGroup?: (s: SlashSource) => void;
  } = {},
) {
  const groups = groupSlashItems(items, query);
  const searching = query.trim() !== "";
  const handlers = {
    onSelect: opts.onSelect ?? (() => {}),
    onToggleGroup: opts.onToggleGroup ?? (() => {}),
  };
  return render(
    <SlashAutocomplete
      groups={groups}
      searching={searching}
      collapsed={opts.collapsed ?? noCollapse}
      selectedIndex={opts.selectedIndex ?? 0}
      onSelect={handlers.onSelect}
      onToggleGroup={handlers.onToggleGroup}
    />,
  );
}

describe("SlashAutocomplete", () => {
  it("groups by source in fixed order when query is empty", () => {
    renderPicker("");
    const labels = screen
      .getAllByRole("button")
      .map((b) => b.textContent?.replace(/[▾▸]/g, "").trim());
    expect(labels).toEqual([
      "builtin · 1",
      "bundled · 1",
      "user · 1",
      "project · 1",
      "plugin · 1",
    ]);
  });

  it("renders every item when nothing is collapsed", () => {
    renderPicker("");
    expect(screen.getByText(/deep-research/)).toBeInTheDocument();
    expect(screen.getByText(/grill-me/)).toBeInTheDocument();
    expect(screen.getByText(/deploy/)).toBeInTheDocument();
    expect(screen.getByText(/code-review/)).toBeInTheDocument();
  });

  it("collapsed plugin group hides its items but keeps the header", () => {
    renderPicker("", { collapsed: new Set(["plugin"]) });
    expect(screen.getByRole("button", { name: /plugin 组/ })).toBeInTheDocument();
    expect(screen.queryByText(/code-review/)).not.toBeInTheDocument();
    expect(screen.getByText(/grill-me/)).toBeInTheDocument();
  });

  it("searching forces the plugin group open even if collapsed", () => {
    renderPicker("review", { collapsed: new Set(["plugin"]) });
    expect(screen.getByText(/code-review/)).toBeInTheDocument();
  });

  it("group header click fires onToggleGroup with that source", () => {
    const onToggle = vi.fn();
    renderPicker("", { onToggleGroup: onToggle });
    fireEvent.click(screen.getByRole("button", { name: /plugin 组/ }));
    expect(onToggle).toHaveBeenCalledWith("plugin");
  });

  it("marks the selected option via aria-selected (flat expanded order)", () => {
    renderPicker("", { selectedIndex: 2 });
    const opts = screen.getAllByRole("option");
    expect(opts[2]).toHaveAttribute("aria-selected", "true");
    expect(opts[0]).toHaveAttribute("aria-selected", "false");
  });

  it("click an option calls onSelect with the full item (plugin keeps full name)", () => {
    const onSelect = vi.fn();
    renderPicker("", { onSelect });
    fireEvent.click(screen.getByText(/code-review/));
    expect(onSelect).toHaveBeenCalledWith(
      items.find((i) => i.source === "plugin"),
    );
  });

  it("shows description + a per-source badge", () => {
    renderPicker("");
    expect(screen.getByText("切换模型")).toBeInTheDocument();
    expect(screen.getByText("Expert code review")).toBeInTheDocument();
    expect(screen.getByText("builtin")).toBeInTheDocument();
    expect(screen.getByText("user")).toBeInTheDocument();
    expect(screen.getByText("plugin")).toBeInTheDocument();
  });

  it("dims the plugin prefix in its own span (C-4 visual)", () => {
    const { container } = renderPicker("");
    const pre = container.querySelector(".cc-ac__pre");
    expect(pre?.textContent).toBe("everything-claude-code:");
  });

  it("renders nothing when no items match the query", () => {
    const { container } = renderPicker("zzzzz");
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when groups prop is empty", () => {
    const { container } = render(
      <SlashAutocomplete
        groups={[]}
        searching={false}
        collapsed={noCollapse}
        selectedIndex={0}
        onSelect={() => {}}
        onToggleGroup={() => {}}
      />,
    );
    expect(container.firstChild).toBeNull();
  });
});
