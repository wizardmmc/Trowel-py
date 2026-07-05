import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SlashAutocomplete } from "../components/cc/SlashAutocomplete";
import type { SlashItem } from "../api/cc";

const items: readonly SlashItem[] = [
  { name: "monthly-etf", description: "月度ETF推荐", source: "user", type: "skill" },
  { name: "deploy", description: "部署到生产", source: "project", type: "command" },
  { name: "review", description: "code review", source: "bundled", type: "skill" },
];

describe("SlashAutocomplete (slice-027 C1)", () => {
  it("renders all items grouped by type when query is empty", () => {
    render(
      <SlashAutocomplete query="" items={items} selectedIndex={0} onSelect={() => {}} />,
    );
    expect(screen.getByText("/monthly-etf")).toBeInTheDocument();
    expect(screen.getByText("/deploy")).toBeInTheDocument();
    // group labels present
    expect(screen.getByText(/skills/i)).toBeInTheDocument();
    expect(screen.getByText(/commands/i)).toBeInTheDocument();
  });

  it("filters by prefix match on name (case-insensitive)", () => {
    render(
      <SlashAutocomplete query="MON" items={items} selectedIndex={0} onSelect={() => {}} />,
    );
    expect(screen.getByText("/monthly-etf")).toBeInTheDocument();
    expect(screen.queryByText("/deploy")).not.toBeInTheDocument();
    expect(screen.queryByText("/review")).not.toBeInTheDocument();
  });

  it("substring match also works (not just prefix)", () => {
    render(
      <SlashAutocomplete query="view" items={items} selectedIndex={0} onSelect={() => {}} />,
    );
    expect(screen.getByText("/review")).toBeInTheDocument();
  });

  it("marks the selected option via aria-selected", () => {
    render(
      <SlashAutocomplete query="" items={items} selectedIndex={1} onSelect={() => {}} />,
    );
    const opts = screen.getAllByRole("option");
    expect(opts[1]).toHaveAttribute("aria-selected", "true");
    expect(opts[0]).toHaveAttribute("aria-selected", "false");
  });

  it("click an option calls onSelect with the item", () => {
    const onSelect = vi.fn();
    render(
      <SlashAutocomplete query="" items={items} selectedIndex={0} onSelect={onSelect} />,
    );
    fireEvent.click(screen.getByText("/deploy"));
    expect(onSelect).toHaveBeenCalledWith(items[1]);
  });

  it("shows description + source badge", () => {
    render(
      <SlashAutocomplete query="" items={items} selectedIndex={0} onSelect={() => {}} />,
    );
    expect(screen.getByText("月度ETF推荐")).toBeInTheDocument();
    expect(screen.getByText("user")).toBeInTheDocument();
    expect(screen.getByText("project")).toBeInTheDocument();
    expect(screen.getByText("bundled")).toBeInTheDocument();
  });

  it("renders nothing when no items match", () => {
    const { container } = render(
      <SlashAutocomplete query="zzz" items={items} selectedIndex={0} onSelect={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when items prop is empty", () => {
    const { container } = render(
      <SlashAutocomplete query="" items={[]} selectedIndex={0} onSelect={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });
});
