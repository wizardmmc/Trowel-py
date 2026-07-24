import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ToolBlock } from "../components/cc/ToolBlock";
import { tool } from "./toolBlockFixtures";

describe("ToolBlock — Codex apply_patch rendering", () => {
  it("renders update verb, mixed stat and diff detail", () => {
    const { container } = render(
      <ToolBlock
        item={tool({
          toolName: "apply_patch",
          input: { paths: ["/a/g.txt"], change_kinds: ["modify"] },
          status: "done",
          writeDiff: {
            type: "update",
            hunks: [
              {
                oldStart: 1,
                oldLines: 1,
                newStart: 1,
                newLines: 1,
                lines: ["-hi", "+hey"],
              },
            ],
          },
          nativeStatus: "completed",
        })}
      />,
    );
    expect(screen.getByText("Update")).toBeTruthy();
    expect(screen.getByText("+1")).toBeTruthy();
    expect(screen.getByText("−1")).toBeTruthy();
    expect(container.querySelector(".cc-tool__detail")).toBeTruthy();
  });

  it("renders create verb and add-only stat", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "apply_patch",
          input: { paths: ["/a/new.txt"], change_kinds: ["add"] },
          status: "done",
          writeDiff: {
            type: "create",
            hunks: [
              {
                oldStart: 0,
                oldLines: 0,
                newStart: 1,
                newLines: 2,
                lines: ["+hello", "+world"],
              },
            ],
          },
          nativeStatus: "completed",
        })}
      />,
    );
    expect(screen.getByText("Create")).toBeTruthy();
    expect(screen.getByText("+2")).toBeTruthy();
  });

  it("renders delete verb and remove-only stat", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "apply_patch",
          input: { paths: ["/a/old.txt"], change_kinds: ["delete"] },
          status: "done",
          writeDiff: {
            type: "delete",
            hunks: [
              {
                oldStart: 1,
                oldLines: 2,
                newStart: 0,
                newLines: 0,
                lines: ["-a", "-b"],
              },
            ],
          },
          nativeStatus: "completed",
        })}
      />,
    );
    expect(screen.getByText("Delete")).toBeTruthy();
    expect(screen.getByText("−2")).toBeTruthy();
  });

  it("renders declined patch as failed without green check", () => {
    const { container } = render(
      <ToolBlock
        item={tool({
          toolName: "apply_patch",
          input: { paths: ["/a/x.txt"], change_kinds: ["add"] },
          status: "failed",
          writeDiff: { type: "create", hunks: [] },
          nativeStatus: "declined",
        })}
      />,
    );
    expect(container.querySelector(".cc-tool__check--failed")).toBeTruthy();
    expect(container.querySelector('[aria-label="完成"]')).toBeNull();
  });

  it("shows a project-relative target path", () => {
    render(
      <ToolBlock
        workdir="/workspace/proj"
        item={tool({
          toolName: "apply_patch",
          input: {
            paths: ["/workspace/proj/web/x.ts"],
            change_kinds: ["modify"],
          },
          status: "done",
          writeDiff: {
            type: "update",
            hunks: [
              {
                oldStart: 1,
                oldLines: 1,
                newStart: 1,
                newLines: 1,
                lines: ["-a", "+b"],
              },
            ],
          },
          nativeStatus: "completed",
        })}
      />,
    );
    expect(screen.getByText("web/x.ts")).toBeTruthy();
  });
});
