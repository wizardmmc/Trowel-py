import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { SlashItem } from "../api/cc";
import { Composer } from "../components/cc/Composer";

describe("Composer Esc three-state", () => {
  it("Enter sends and clears the input", () => {
    const onSend = vi.fn();
    render(<Composer streaming={false} disabled={false} onSend={onSend} onInterrupt={() => {}} />);
    const ta = screen.getByLabelText("CC 消息输入") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "hi" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("hi");
    expect(ta.value).toBe("");
  });

  it("Shift+Enter does NOT send (newline passthrough)", () => {
    const onSend = vi.fn();
    render(<Composer streaming={false} disabled={false} onSend={onSend} onInterrupt={() => {}} />);
    const ta = screen.getByLabelText("CC 消息输入");
    fireEvent.change(ta, { target: { value: "hi" } });
    fireEvent.keyDown(ta, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("Esc clears input when there is text (most-specific first)", () => {
    const onInterrupt = vi.fn();
    render(<Composer streaming={true} disabled={false} onSend={() => {}} onInterrupt={onInterrupt} />);
    const ta = screen.getByLabelText("CC 消息输入") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "x" } });
    fireEvent.keyDown(ta, { key: "Escape" });
    expect(ta.value).toBe("");
    expect(onInterrupt).not.toHaveBeenCalled();
  });

  it("Esc interrupts when input is empty and streaming", () => {
    const onInterrupt = vi.fn();
    render(<Composer streaming={true} disabled={false} onSend={() => {}} onInterrupt={onInterrupt} />);
    fireEvent.keyDown(screen.getByLabelText("CC 消息输入"), { key: "Escape" });
    expect(onInterrupt).toHaveBeenCalled();
  });

  it("Esc does nothing when idle and input empty", () => {
    const onInterrupt = vi.fn();
    render(<Composer streaming={false} disabled={false} onSend={() => {}} onInterrupt={onInterrupt} />);
    fireEvent.keyDown(screen.getByLabelText("CC 消息输入"), { key: "Escape" });
    expect(onInterrupt).not.toHaveBeenCalled();
  });

  it("disabled composer blocks send", () => {
    const onSend = vi.fn();
    render(<Composer streaming={false} disabled={true} onSend={onSend} onInterrupt={() => {}} />);
    const ta = screen.getByLabelText("CC 消息输入") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "hi" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });
});

describe("Composer slash autocomplete (slice-027)", () => {
  const items: readonly SlashItem[] = [
    { name: "monthly-etf", description: "月度ETF", source: "user", type: "skill" },
    { name: "review", description: "code review", source: "bundled", type: "skill" },
  ];

  it("shows the autocomplete list when input starts with /", () => {
    render(
      <Composer streaming={false} disabled={false} onSend={() => {}}
                onInterrupt={() => {}} slashItems={items} />,
    );
    const ta = screen.getByLabelText("CC 消息输入");
    fireEvent.change(ta, { target: { value: "/mon" } });
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    expect(screen.getByText("/monthly-etf")).toBeInTheDocument();
  });

  it("does NOT show autocomplete when input is not a slash", () => {
    render(
      <Composer streaming={false} disabled={false} onSend={() => {}}
                onInterrupt={() => {}} slashItems={items} />,
    );
    fireEvent.change(screen.getByLabelText("CC 消息输入"),
      { target: { value: "hello" } });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("ArrowDown + Enter fills input with /name + space (no send)", () => {
    const onSend = vi.fn();
    render(
      <Composer streaming={false} disabled={false} onSend={onSend}
                onInterrupt={() => {}} slashItems={items} />,
    );
    const ta = screen.getByLabelText("CC 消息输入") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "/" } });
    // slice-042: rows are grouped by source (bundled before user), so the flat
    // order is [review(bundled), monthly-etf(user)]. ArrowDown 0→1 lands on
    // monthly-etf.
    fireEvent.keyDown(ta, { key: "ArrowDown" });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(ta.value).toBe("/monthly-etf ");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("Esc closes autocomplete without clearing text", () => {
    render(
      <Composer streaming={false} disabled={false} onSend={() => {}}
                onInterrupt={() => {}} slashItems={items} />,
    );
    const ta = screen.getByLabelText("CC 消息输入") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "/mon" } });
    fireEvent.keyDown(ta, { key: "Escape" });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(ta.value).toBe("/mon");
  });

  it("bare /model + Enter requests the model picker (no send)", () => {
    const onModel = vi.fn();
    const onSend = vi.fn();
    render(
      <Composer streaming={false} disabled={false} onSend={onSend}
                onInterrupt={() => {}} slashItems={items}
                onRequestModelPicker={onModel} />,
    );
    const ta = screen.getByLabelText("CC 消息输入");
    fireEvent.change(ta, { target: { value: "/model" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onModel).toHaveBeenCalledTimes(1);
    expect(onSend).not.toHaveBeenCalled();
  });

  it("bare /effort + Enter requests the effort picker", () => {
    const onEffort = vi.fn();
    render(
      <Composer streaming={false} disabled={false} onSend={() => {}}
                onInterrupt={() => {}} slashItems={items}
                onRequestEffortPicker={onEffort} />,
    );
    const ta = screen.getByLabelText("CC 消息输入");
    fireEvent.change(ta, { target: { value: "/effort" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onEffort).toHaveBeenCalledTimes(1);
  });

  it("works without slashItems (legacy — raw post, no autocomplete)", () => {
    const onSend = vi.fn();
    render(
      <Composer streaming={false} disabled={false} onSend={onSend}
                onInterrupt={() => {}} />,
    );
    const ta = screen.getByLabelText("CC 消息输入");
    fireEvent.change(ta, { target: { value: "/monthly-etf args" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("/monthly-etf args");
  });

  it("clicking a /model row opens the picker (not fill, not send)", () => {
    const onModel = vi.fn();
    const onSend = vi.fn();
    const builtin: readonly SlashItem[] = [
      { name: "model", description: "切换模型", source: "builtin", type: "command" },
      { name: "monthly-etf", description: "月度ETF", source: "user", type: "skill" },
    ];
    render(
      <Composer streaming={false} disabled={false} onSend={onSend}
                onInterrupt={() => {}} slashItems={builtin}
                onRequestModelPicker={onModel} />,
    );
    fireEvent.change(screen.getByLabelText("CC 消息输入"),
      { target: { value: "/model" } });
    fireEvent.click(screen.getByRole("option"));
    expect(onModel).toHaveBeenCalledTimes(1);
    expect(onSend).not.toHaveBeenCalled();
  });

  it("bare /cost Enter sends immediately (no fill, no second Enter)", () => {
    const onSend = vi.fn();
    const builtin: readonly SlashItem[] = [
      { name: "cost", description: "显示花费", source: "builtin", type: "command" },
    ];
    render(
      <Composer streaming={false} disabled={false} onSend={onSend}
                onInterrupt={() => {}} slashItems={builtin} />,
    );
    const ta = screen.getByLabelText("CC 消息输入");
    fireEvent.change(ta, { target: { value: "/cost" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("/cost");
    expect((ta as HTMLTextAreaElement).value).toBe("");
  });

  it("/skill-name with args Enter sends (falls through when no match)", () => {
    const onSend = vi.fn();
    const itemsWithEtf: readonly SlashItem[] = [
      { name: "monthly-etf", description: "月度ETF", source: "user", type: "skill" },
    ];
    render(
      <Composer streaming={false} disabled={false} onSend={onSend}
                onInterrupt={() => {}} slashItems={itemsWithEtf} />,
    );
    const ta = screen.getByLabelText("CC 消息输入");
    fireEvent.change(ta, { target: { value: "/monthly-etf 查看沪深300" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("/monthly-etf 查看沪深300");
  });
});

describe("Composer slash autocomplete (slice-042 P4: source grouping + collapse)", () => {
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
      <Composer streaming={false} disabled={false} onSend={() => {}}
                onInterrupt={() => {}} slashItems={items} />,
    );
    fireEvent.change(screen.getByLabelText("CC 消息输入"),
      { target: { value: "/" } });
    expect(screen.queryByText(/code-review/)).not.toBeInTheDocument();
    expect(screen.queryByText(/rescue/)).not.toBeInTheDocument();
    // non-plugin still present
    expect(screen.getByText(/model/)).toBeInTheDocument();
  });

  it("clicking the plugin group header expands it", () => {
    render(
      <Composer streaming={false} disabled={false} onSend={() => {}}
                onInterrupt={() => {}} slashItems={items} />,
    );
    fireEvent.change(screen.getByLabelText("CC 消息输入"),
      { target: { value: "/" } });
    fireEvent.click(screen.getByRole("button", { name: /plugin 组/ }));
    expect(screen.getByText(/code-review/)).toBeInTheDocument();
  });

  it("ArrowDown is clamped to visible rows — collapsed plugin is unreachable", () => {
    const onSend = vi.fn();
    render(
      <Composer streaming={false} disabled={false} onSend={onSend}
                onInterrupt={() => {}} slashItems={items} />,
    );
    const ta = screen.getByLabelText("CC 消息输入") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "/" } });
    // spam ArrowDown past the single visible row — index must clamp at 0,
    // never descend into the hidden plugin rows.
    fireEvent.keyDown(ta, { key: "ArrowDown" });
    fireEvent.keyDown(ta, { key: "ArrowDown" });
    fireEvent.keyDown(ta, { key: "ArrowDown" });
    fireEvent.keyDown(ta, { key: "Enter" });
    // clamped to model (index 0) → fills /model  ... but model opens no picker
    // here (no onRequestModelPicker), so pickItem fills "/model ".
    expect(ta.value).toBe("/model ");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("searching expands the plugin group so matches are pickable", () => {
    const onSend = vi.fn();
    render(
      <Composer streaming={false} disabled={false} onSend={onSend}
                onInterrupt={() => {}} slashItems={items} />,
    );
    const ta = screen.getByLabelText("CC 消息输入") as HTMLTextAreaElement;
    // "review" matches only the plugin item; even though plugin is collapsed
    // by default, searching forces it open.
    fireEvent.change(ta, { target: { value: "/review" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(ta.value).toBe("/everything-claude-code:code-review ");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("collapsing a group that holds the highlight clamps it back into view", () => {
    // Guards the HIGH review finding: stale acIndex after a collapse must not
    // make the highlight vanish.
    render(
      <Composer streaming={false} disabled={false} onSend={() => {}}
                onInterrupt={() => {}} slashItems={items} />,
    );
    const ta = screen.getByLabelText("CC 消息输入");
    fireEvent.change(ta, { target: { value: "/" } });
    // expand plugin → flat = [model, codex:rescue, ecc:code-review]
    // (localeCompare: "codex:rescue" < "everything-claude-code:code-review")
    fireEvent.click(screen.getByRole("button", { name: /plugin 组/ }));
    fireEvent.keyDown(ta, { key: "ArrowDown" }); // → codex:rescue
    fireEvent.keyDown(ta, { key: "ArrowDown" }); // → ecc:code-review (acIndex 2)
    // collapse plugin again → flat shrinks to [model]; clamp must move index to 0
    fireEvent.click(screen.getByRole("button", { name: /plugin 组/ }));
    const opts = screen.getAllByRole("option");
    expect(opts).toHaveLength(1);
    expect(opts[0]).toHaveAttribute("aria-selected", "true");
  });

  it("keyboard index lines up with the rendered row across multi-item groups", () => {
    // Adversarial alignment check: 2 items per group, navigate to a known
    // flat index, assert the aria-selected row is the one acFlat points at.
    const multi: readonly SlashItem[] = [
      { name: "model", description: "", source: "builtin", type: "command" },
      { name: "status", description: "", source: "builtin", type: "command" },
      { name: "grill-me", description: "", source: "user", type: "skill" },
      { name: "monthly-etf", description: "", source: "user", type: "skill" },
    ];
    render(
      <Composer streaming={false} disabled={false} onSend={() => {}}
                onInterrupt={() => {}} slashItems={multi} />,
    );
    const ta = screen.getByLabelText("CC 消息输入");
    fireEvent.change(ta, { target: { value: "/" } });
    // flat order: model, status, grill-me, monthly-etf. Two ArrowDowns → index 2.
    fireEvent.keyDown(ta, { key: "ArrowDown" });
    fireEvent.keyDown(ta, { key: "ArrowDown" });
    const opts = screen.getAllByRole("option");
    expect(opts[2]).toHaveAttribute("aria-selected", "true");
    expect(opts[2].textContent).toMatch(/grill-me/);
  });
});
