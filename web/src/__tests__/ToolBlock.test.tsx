import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ToolBlock } from "../components/cc/ToolBlock";
import type { ToolItem } from "../stores/ccStore";
import type { WriteDiff } from "../api/ccTypes";

function tool(over: Partial<ToolItem> = {}): ToolItem {
  return {
    kind: "tool",
    toolUseId: "t1",
    toolName: "Write",
    input: { file_path: "/a/b.txt", content: "x" },
    status: "running",
    elapsedSeconds: null,
    result: null,
    childTools: [],
    ...over,
  };
}

describe("ToolBlock — regression (non-diff tools unchanged)", () => {
  it("Write tool shows the path in the summary", () => {
    render(<ToolBlock item={tool({ toolName: "Write", input: { file_path: "/a/b.txt" } })} />);
    expect(screen.getByText("/a/b.txt")).toBeTruthy();
  });

  it("Bash tool shows the command in the summary", () => {
    render(
      <ToolBlock item={tool({ toolName: "Bash", input: { command: "echo hi" } })} />,
    );
    expect(screen.getByText("echo hi")).toBeTruthy();
  });

  it("other tool (Grep) falls back to a JSON tree after expand", () => {
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
    // collapsed by default — summary shows the tool name (no verb mapping for Grep)
    expect(screen.getAllByText("Grep").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText(/"pattern": "foo"/)).toBeTruthy();
  });

  it("done tool shows a check + elapsed time", () => {
    render(<ToolBlock item={tool({ status: "done", elapsedSeconds: 0.8, result: "ok" })} />);
    expect(screen.getByText("0.8s")).toBeTruthy();
    expect(screen.getByLabelText("完成")).toBeTruthy();
  });

  it("running tool with elapsed shows elapsed but no check", () => {
    render(<ToolBlock item={tool({ status: "running", elapsedSeconds: 1.2 })} />);
    expect(screen.getByText("1.2s")).toBeTruthy();
    expect(screen.queryByLabelText("完成")).toBeNull();
  });
});

describe("ToolBlock — slice-029 Edit/Write rendering", () => {
  it("Edit done maps to the 'Update' verb with +N −M stat", () => {
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

  it("Edit with empty old_string maps to 'Create'", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Edit",
          input: { file_path: "/new.ts", old_string: "", new_string: "a\nb\n" },
          status: "done",
          elapsedSeconds: 0.1,
        })}
      />,
    );
    expect(screen.getByText("Create")).toBeTruthy();
  });

  it("MultiEdit done maps to 'Update' with aggregated stat", () => {
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

  it("Edit expand shows the 'Added N lines' sentence + diff lines", () => {
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
    fireEvent.click(screen.getByRole("button"));
    // CC phrasing: lowercase "removed" when add > 0 (FileEditToolUpdatedMessage).
    expect(screen.getByText(/Added 1 line, removed 1 line/)).toBeTruthy();
    // a removed and an added content line both render
    const detail = document.querySelector(".cc-tool__detail");
    expect(detail!.querySelector('.cc-tool__diff-line[data-type="remove"]')).toBeTruthy();
    expect(detail!.querySelector('.cc-tool__diff-line[data-type="add"]')).toBeTruthy();
  });

  it("Edit done with no expandable detail when input is identical (no diff)", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Edit",
          input: { file_path: "/a/b.ts", old_string: "same", new_string: "same" },
          status: "done",
          elapsedSeconds: 0.2,
        })}
      />,
    );
    // verb still rendered; stat pill absent (no change to count)
    expect(screen.getByText("Update")).toBeTruthy();
    expect(screen.queryByText(/^\+0$/)).toBeNull();
  });

  it("Write-create (no writeDiff) shows 'Write' + +N stat, expand shows 'Wrote N lines'", () => {
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
    fireEvent.click(screen.getByRole("button"));
    // "Wrote <b>3</b> lines to <b>new.ts</b>" — <b> splits the text node.
    const create = document.querySelector(".cc-tool__create-lines");
    expect(create!.textContent).toMatch(/Wrote 3 lines/);
  });

  it("Write-create caps the preview at 10 lines with '+M more lines'", () => {
    const content = Array.from({ length: 13 }, (_, i) => `line${i}`).join("\n") + "\n";
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
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText(/\+3 more lines/)).toBeTruthy();
  });

  it("Write-overwrite (writeDiff.type='update') shows +N −M and diff body", () => {
    const wd: WriteDiff = {
      type: "update",
      hunks: [
        {
          oldStart: 1, oldLines: 2, newStart: 1, newLines: 2,
          lines: [" ctx", "-old", "+new"],
        },
      ],
    };
    render(
      <ToolBlock
        item={tool({
          toolName: "Write",
          input: { file_path: "/x/y.ts", content: "fresh" },
          writeDiff: wd,
          status: "done",
          elapsedSeconds: 0.6,
        })}
      />,
    );
    expect(screen.getByText("+1")).toBeTruthy();
    expect(screen.getByText("−1")).toBeTruthy();
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText(/Added 1 line, removed 1 line/)).toBeTruthy();
  });

  it("running Edit shows a spinner (进行中) and no stat pill", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Edit",
          input: { file_path: "/a.ts", old_string: "a", new_string: "b" },
          status: "running",
          elapsedSeconds: null,
        })}
      />,
    );
    expect(screen.getByLabelText("进行中")).toBeTruthy();
    expect(screen.queryByText(/^\+\d/)).toBeNull();
  });

  it("shows project-relative path when workdir is provided (CC getDisplayPath)", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Edit",
          input: {
            file_path: "/Users/me/proj/web/x.ts",
            old_string: "a",
            new_string: "b",
          },
          status: "done",
          elapsedSeconds: 0.2,
        })}
        workdir="/Users/me/proj"
      />,
    );
    expect(screen.getByText("web/x.ts")).toBeTruthy();
    expect(screen.queryByText("/Users/me/proj/web/x.ts")).toBeNull();
  });

  it("falls back to absolute path when file is outside workdir", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Edit",
          input: { file_path: "/elsewhere/x.ts", old_string: "a", new_string: "b" },
          status: "done",
          elapsedSeconds: 0.2,
        })}
        workdir="/Users/me/proj"
      />,
    );
    expect(screen.getByText("/elsewhere/x.ts")).toBeTruthy();
  });

  it("condensed mode renders no detail and no button (non-interactive)", () => {
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
    // No button role — condensed renders a <div>, not a clickable button.
    expect(screen.queryByRole("button")).toBeNull();
    expect(document.querySelector(".cc-tool__detail")).toBeNull();
  });
});
