import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SlashItem } from "../api/cc";
import { Composer } from "../components/cc/Composer";

describe("Composer slash autocomplete", () => {
  const items: readonly SlashItem[] = [
    { name: "model", description: "切换模型", source: "builtin", type: "command" },
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

  it("plugin group is collapsed by default — its items are not rendered", () => {
    render(
      <Composer
        streaming={false}
        disabled={false}
        onSend={() => {}}
        onInterrupt={() => {}}
        slashItems={items}
      />,
    );
    fireEvent.change(screen.getByLabelText("CC 消息输入"), { target: { value: "/" } });
    expect(screen.queryByText(/code-review/)).not.toBeInTheDocument();
    expect(screen.queryByText(/rescue/)).not.toBeInTheDocument();
    expect(screen.getByText(/model/)).toBeInTheDocument();
  });

  it("clicking the plugin group header expands it", () => {
    render(
      <Composer
        streaming={false}
        disabled={false}
        onSend={() => {}}
        onInterrupt={() => {}}
        slashItems={items}
      />,
    );
    fireEvent.change(screen.getByLabelText("CC 消息输入"), { target: { value: "/" } });
    fireEvent.click(screen.getByRole("button", { name: /plugin 组/ }));
    expect(screen.getByText(/code-review/)).toBeInTheDocument();
  });

  it("ArrowDown is clamped to visible rows — collapsed plugin is unreachable", () => {
    const onSend = vi.fn();
    render(
      <Composer
        streaming={false}
        disabled={false}
        onSend={onSend}
        onInterrupt={() => {}}
        slashItems={items}
      />,
    );
    const ta = screen.getByLabelText("CC 消息输入") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "/" } });
    fireEvent.keyDown(ta, { key: "ArrowDown" });
    fireEvent.keyDown(ta, { key: "ArrowDown" });
    fireEvent.keyDown(ta, { key: "ArrowDown" });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(ta.value).toBe("/model ");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("searching expands the plugin group so matches are pickable", () => {
    const onSend = vi.fn();
    render(
      <Composer
        streaming={false}
        disabled={false}
        onSend={onSend}
        onInterrupt={() => {}}
        slashItems={items}
      />,
    );
    const ta = screen.getByLabelText("CC 消息输入") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "/review" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(ta.value).toBe("/everything-claude-code:code-review ");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("collapsing a group that holds the highlight clamps it back into view", () => {
    render(
      <Composer
        streaming={false}
        disabled={false}
        onSend={() => {}}
        onInterrupt={() => {}}
        slashItems={items}
      />,
    );
    const ta = screen.getByLabelText("CC 消息输入");
    fireEvent.change(ta, { target: { value: "/" } });
    fireEvent.click(screen.getByRole("button", { name: /plugin 组/ }));
    fireEvent.keyDown(ta, { key: "ArrowDown" });
    fireEvent.keyDown(ta, { key: "ArrowDown" });
    fireEvent.click(screen.getByRole("button", { name: /plugin 组/ }));
    const opts = screen.getAllByRole("option");
    expect(opts).toHaveLength(1);
    expect(opts[0]).toHaveAttribute("aria-selected", "true");
  });

  it("keyboard index lines up with the rendered row across multi-item groups", () => {
    const multi: readonly SlashItem[] = [
      { name: "model", description: "", source: "builtin", type: "command" },
      { name: "status", description: "", source: "builtin", type: "command" },
      { name: "grill-me", description: "", source: "user", type: "skill" },
      { name: "monthly-etf", description: "", source: "user", type: "skill" },
    ];
    render(
      <Composer
        streaming={false}
        disabled={false}
        onSend={() => {}}
        onInterrupt={() => {}}
        slashItems={multi}
      />,
    );
    const ta = screen.getByLabelText("CC 消息输入");
    fireEvent.change(ta, { target: { value: "/" } });
    fireEvent.keyDown(ta, { key: "ArrowDown" });
    fireEvent.keyDown(ta, { key: "ArrowDown" });
    const opts = screen.getAllByRole("option");
    expect(opts[2]).toHaveAttribute("aria-selected", "true");
    expect(opts[2].textContent).toMatch(/grill-me/);
  });
});
