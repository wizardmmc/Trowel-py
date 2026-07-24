import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { WriteDiff } from "../api/ccTypes";
import { ToolBlock } from "../components/cc/ToolBlock";
import { tool } from "./toolBlockFixtures";

describe("ToolBlock — Edit/Write rendering", () => {
  it("Edit done maps to the Update verb with +N −M stat", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Edit",
          input: {
            file_path: "/a/b.ts",
            old_string: "alpha\nbeta\ngamma\n",
            new_string: "alpha\nBETA\ngamma\n",
          },
          status: "done",
          elapsedSeconds: 0.5,
        })}
      />,
    );
    expect(screen.getByText("Update")).toBeTruthy();
    expect(screen.getByText("+1")).toBeTruthy();
    expect(screen.getByText("−1")).toBeTruthy();
  });

  it("Edit with empty old_string maps to Create", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Edit",
          input: {
            file_path: "/new.ts",
            old_string: "",
            new_string: "a\nb\n",
          },
          status: "done",
          elapsedSeconds: 0.1,
        })}
      />,
    );
    expect(screen.getByText("Create")).toBeTruthy();
  });

  it("MultiEdit done maps to Update with aggregated stat", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "MultiEdit",
          input: {
            file_path: "/a/b.ts",
            edits: [
              { old_string: "x", new_string: "y" },
              { old_string: "p", new_string: "q" },
            ],
          },
          status: "done",
          elapsedSeconds: 0.7,
        })}
      />,
    );
    expect(screen.getByText("Update")).toBeTruthy();
    expect(screen.getByText("+2")).toBeTruthy();
    expect(screen.getByText("−2")).toBeTruthy();
  });

  it("Edit detail shows the stat sentence and diff lines", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Edit",
          input: {
            file_path: "/a/b.ts",
            old_string: "keep\nold\n",
            new_string: "keep\nnew\n",
          },
          status: "done",
          elapsedSeconds: 0.3,
        })}
      />,
    );
    expect(screen.getByText(/Added 1 line, removed 1 line/)).toBeTruthy();
    const detail = document.querySelector(".cc-tool__detail");
    expect(
      detail?.querySelector('.cc-tool__diff-line[data-type="remove"]'),
    ).toBeTruthy();
    expect(
      detail?.querySelector('.cc-tool__diff-line[data-type="add"]'),
    ).toBeTruthy();
  });

  it("Edit with identical input has no stat", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Edit",
          input: {
            file_path: "/a/b.ts",
            old_string: "same",
            new_string: "same",
          },
          status: "done",
          elapsedSeconds: 0.2,
        })}
      />,
    );
    expect(screen.getByText("Update")).toBeTruthy();
    expect(screen.queryByText(/^\+0$/)).toBeNull();
  });

  it("Write create shows line stat and preview", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Write",
          input: { file_path: "/x/new.ts", content: "a\nb\nc\n" },
          status: "done",
          elapsedSeconds: 0.4,
        })}
      />,
    );
    expect(screen.getByText("Write")).toBeTruthy();
    expect(screen.getByText("+3")).toBeTruthy();
    expect(
      document.querySelector(".cc-tool__create-lines")?.textContent,
    ).toMatch(/Wrote 3 lines/);
  });

  it("Write create caps the preview at 10 lines", () => {
    const content =
      `${Array.from({ length: 13 }, (_, index) => `line${index}`).join("\n")}\n`;
    render(
      <ToolBlock
        item={tool({
          toolName: "Write",
          input: { file_path: "/big.ts", content },
          status: "done",
          elapsedSeconds: 0.4,
        })}
      />,
    );
    expect(screen.getByText(/\+3 more lines/)).toBeTruthy();
  });

  it("Write overwrite uses writeDiff for stat and detail", () => {
    const writeDiff: WriteDiff = {
      type: "update",
      hunks: [
        {
          oldStart: 1,
          oldLines: 2,
          newStart: 1,
          newLines: 2,
          lines: [" ctx", "-old", "+new"],
        },
      ],
    };
    render(
      <ToolBlock
        item={tool({
          toolName: "Write",
          input: { file_path: "/x/y.ts", content: "fresh" },
          writeDiff,
          status: "done",
          elapsedSeconds: 0.6,
        })}
      />,
    );
    expect(screen.getByText("+1")).toBeTruthy();
    expect(screen.getByText("−1")).toBeTruthy();
    expect(screen.getByText(/Added 1 line, removed 1 line/)).toBeTruthy();
  });

  it("running Edit shows a spinner and no stat", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Edit",
          input: {
            file_path: "/a.ts",
            old_string: "a",
            new_string: "b",
          },
          status: "running",
          elapsedSeconds: null,
        })}
      />,
    );
    expect(screen.getByLabelText("进行中")).toBeTruthy();
    expect(screen.queryByText(/^\+\d/)).toBeNull();
  });

  it("shows project-relative paths inside workdir", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Edit",
          input: {
            file_path: "/workspace/proj/web/x.ts",
            old_string: "a",
            new_string: "b",
          },
          status: "done",
          elapsedSeconds: 0.2,
        })}
        workdir="/workspace/proj"
      />,
    );
    expect(screen.getByText("web/x.ts")).toBeTruthy();
    expect(screen.queryByText("/workspace/proj/web/x.ts")).toBeNull();
  });

  it("keeps absolute paths outside workdir", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Edit",
          input: {
            file_path: "/elsewhere/x.ts",
            old_string: "a",
            new_string: "b",
          },
          status: "done",
          elapsedSeconds: 0.2,
        })}
        workdir="/workspace/proj"
      />,
    );
    expect(screen.getByText("/elsewhere/x.ts")).toBeTruthy();
  });

  it("condensed mode has no detail or button", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Edit",
          input: {
            file_path: "/a/b.ts",
            old_string: "old\n",
            new_string: "new\n",
          },
          status: "done",
          elapsedSeconds: 0.3,
        })}
        condensed
      />,
    );
    expect(screen.queryByRole("button")).toBeNull();
    expect(document.querySelector(".cc-tool__detail")).toBeNull();
  });
});
