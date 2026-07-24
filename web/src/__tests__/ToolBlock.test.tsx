import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { WriteDiff } from "../api/ccTypes";
import { ToolBlock } from "../components/cc/ToolBlock";
import { tool } from "./toolBlockFixtures";

describe("ToolBlock — summary and fallback", () => {
  it("shows a Write path", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Write",
          input: { file_path: "/a/b.txt" },
        })}
      />,
    );
    expect(screen.getByText("/a/b.txt")).toBeTruthy();
  });

  it("shows a Bash command", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Bash",
          input: { command: "echo hi" },
        })}
      />,
    );
    expect(screen.getByText("echo hi")).toBeTruthy();
  });

  it("falls back to JSON detail for other tools", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Grep",
          input: { pattern: "foo" },
          result: "match",
          status: "done",
        })}
      />,
    );
    expect(screen.getAllByText("Grep").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText(/"pattern": "foo"/)).toBeTruthy();
  });

  it("shows completion and elapsed time", () => {
    render(
      <ToolBlock
        item={tool({
          status: "done",
          elapsedSeconds: 0.8,
          result: "ok",
        })}
      />,
    );
    expect(screen.getByText("0.8s")).toBeTruthy();
    expect(screen.getByLabelText("完成")).toBeTruthy();
  });

  it("shows running elapsed time without completion", () => {
    render(
      <ToolBlock
        item={tool({ status: "running", elapsedSeconds: 1.2 })}
      />,
    );
    expect(screen.getByText("1.2s")).toBeTruthy();
    expect(screen.queryByLabelText("完成")).toBeNull();
  });
});

describe("ToolBlock — automatic expansion", () => {
  let scrollIntoView: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView =
      scrollIntoView as typeof Element.prototype.scrollIntoView;
    vi.stubGlobal(
      "requestAnimationFrame",
      vi.fn((callback: FrameRequestCallback) => {
        callback(0);
        return 0;
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
  });

  it("does not scroll for an initially completed diff", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Edit",
          status: "done",
          input: {
            file_path: "/a",
            old_string: "x",
            new_string: "y",
          },
        })}
      />,
    );
    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it("scrolls a running to done expansion into view", () => {
    const { rerender } = render(
      <ToolBlock
        item={tool({
          toolName: "Edit",
          status: "running",
          input: {
            file_path: "/a",
            old_string: "x",
            new_string: "y",
          },
        })}
      />,
    );
    rerender(
      <ToolBlock
        item={tool({
          toolName: "Edit",
          status: "done",
          input: {
            file_path: "/a",
            old_string: "x",
            new_string: "y",
          },
        })}
      />,
    );
    expect(scrollIntoView).toHaveBeenCalledWith({
      block: "nearest",
      behavior: "smooth",
    });
  });

  it("auto-expands a completed Edit", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Edit",
          input: {
            file_path: "/a/b.ts",
            old_string: "a",
            new_string: "b",
          },
          status: "done",
          elapsedSeconds: 0.3,
        })}
      />,
    );
    expect(document.querySelector(".cc-tool__detail")).toBeTruthy();
  });

  it("keeps a running Edit collapsed", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Edit",
          input: {
            file_path: "/a/b.ts",
            old_string: "a",
            new_string: "b",
          },
          status: "running",
          elapsedSeconds: null,
        })}
      />,
    );
    expect(document.querySelector(".cc-tool__detail")).toBeNull();
  });

  it("does not auto-expand completed Bash", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Bash",
          input: { command: "echo hi" },
          status: "done",
          elapsedSeconds: 0.1,
        })}
      />,
    );
    expect(document.querySelector(".cc-tool__detail")).toBeNull();
  });
});

describe("ToolBlock — Edit diff source", () => {
  it("uses real file line numbers from writeDiff", () => {
    const writeDiff: WriteDiff = {
      type: "update",
      hunks: [
        {
          oldStart: 360,
          oldLines: 2,
          newStart: 360,
          newLines: 3,
          lines: [
            " async def send(self, text):",
            "-    if not sid: return",
            "+    if not sid:",
            "+        return",
          ],
        },
      ],
    };
    render(
      <ToolBlock
        item={tool({
          toolName: "Edit",
          input: {
            file_path: "/a/service.py",
            old_string: "x",
            new_string: "y",
          },
          writeDiff,
          status: "done",
          elapsedSeconds: 0.8,
        })}
      />,
    );
    expect(
      Array.from(document.querySelectorAll(".cc-tool__diff-gutter")).some(
        (gutter) => gutter.textContent === "360",
      ),
    ).toBe(true);
  });

  it("falls back to fragment diff without writeDiff", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Edit",
          input: {
            file_path: "/a/b.ts",
            old_string: "same\nold\n",
            new_string: "same\nnew\n",
          },
          status: "done",
          elapsedSeconds: 0.3,
        })}
      />,
    );
    expect(screen.getByText("+1")).toBeTruthy();
    expect(screen.getByText("−1")).toBeTruthy();
  });
});

describe("ToolBlock — Skill summary", () => {
  it("shows the loaded skill name", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Skill",
          input: { skill: "grill-me" },
          status: "done",
          elapsedSeconds: 0.4,
          result: "ok",
        })}
      />,
    );
    expect(screen.getByText(/加载 skill: grill-me/)).toBeInTheDocument();
  });

  it("has no stat pill", () => {
    const { container } = render(
      <ToolBlock
        item={tool({
          toolName: "Skill",
          input: { skill: "grill-me" },
          status: "done",
          result: "ok",
        })}
      />,
    );
    expect(container.querySelector(".cc-tool__stat")).toBeNull();
  });
});
