import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ToolBlock } from "../components/cc/ToolBlock";
import { CATN_3, tool } from "./toolBlockFixtures";

describe("ToolBlock — Read rendering", () => {
  it("shows path, line count and elapsed time", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Read",
          input: { file_path: "/a/b.py" },
          result: CATN_3,
          status: "done",
          elapsedSeconds: 0.8,
        })}
      />,
    );
    expect(screen.getByText("Read")).toBeTruthy();
    expect(screen.getByText("/a/b.py")).toBeTruthy();
    expect(screen.getByText(/3 lines/)).toBeTruthy();
    expect(screen.getByText("0.8s")).toBeTruthy();
  });

  it("shows a spinner without line count while running", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Read",
          input: { file_path: "/a/b.py" },
          status: "running",
          elapsedSeconds: 0.3,
        })}
      />,
    );
    expect(screen.getByLabelText("进行中")).toBeTruthy();
    expect(screen.queryByText(/lines/)).toBeNull();
    expect(screen.getByText("0.3s")).toBeTruthy();
  });

  it("preserves real line numbers for offset reads", () => {
    const result = "   200\tline A\n   201\tline B\n   202\tline C\n";
    render(
      <ToolBlock
        item={tool({
          toolName: "Read",
          input: { file_path: "/a/big.py", offset: 200 },
          result,
          status: "done",
          elapsedSeconds: 0.5,
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button"));
    const gutters = document.querySelectorAll(".cc-tool__read-gutter");
    expect(Array.from(gutters, (gutter) => gutter.textContent)).toEqual([
      "200",
      "201",
      "202",
    ]);
  });

  it("renders cat -n output as gutter and content without diff markers", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Read",
          input: { file_path: "/a/b.py" },
          result: CATN_3,
          status: "done",
          elapsedSeconds: 0.2,
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button"));
    const detail = document.querySelector(".cc-tool__detail");
    expect(detail?.querySelector(".cc-tool__read-line")).toBeTruthy();
    expect(
      Array.from(
        detail?.querySelectorAll(".cc-tool__read-content") ?? [],
      ).some((content) => content.textContent?.includes("from fastapi")),
    ).toBe(true);
    expect(detail?.querySelector('[data-type="add"]')).toBeNull();
    expect(detail?.querySelector('[data-type="remove"]')).toBeNull();
  });

  it("falls back to pre for non-cat output", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Read",
          input: { file_path: "/a/missing.py" },
          result: "Error: file not found",
          status: "done",
          elapsedSeconds: 0.1,
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button"));
    const detail = document.querySelector(".cc-tool__detail");
    expect(detail?.querySelector(".cc-tool__bash-out")).toBeTruthy();
    expect(detail?.querySelector(".cc-tool__read-line")).toBeNull();
  });

  it("normalizes CRLF before parsing cat -n output", () => {
    const result = "     1\timport os\r\n     2\timport sys\r\n";
    render(
      <ToolBlock
        item={tool({
          toolName: "Read",
          input: { file_path: "/a/win.py" },
          result,
          status: "done",
          elapsedSeconds: 0.2,
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button"));
    const detail = document.querySelector(".cc-tool__detail");
    expect(detail?.querySelector(".cc-tool__read-line")).toBeTruthy();
    expect(detail?.querySelector(".cc-tool__bash-out")).toBeNull();
    expect(
      detail?.querySelector(".cc-tool__read-content")?.textContent,
    ).toBe("import os");
  });

  it("shows a project-relative path inside workdir", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Read",
          input: { file_path: "/workspace/proj/web/x.py" },
          result: CATN_3,
          status: "done",
          elapsedSeconds: 0.2,
        })}
        workdir="/workspace/proj"
      />,
    );
    expect(screen.getByText("web/x.py")).toBeTruthy();
    expect(screen.queryByText("/workspace/proj/web/x.py")).toBeNull();
  });
});
