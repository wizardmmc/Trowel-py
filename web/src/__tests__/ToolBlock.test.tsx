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
    // slice-033 feat 3: Edit done auto-expands — no click needed.
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
    // slice-033 feat 3: Write done auto-expands — no click needed.
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
    // slice-033 feat 3: Write done auto-expands — no click needed.
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
    // slice-033 feat 3: Write done auto-expands — no click needed.
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

// CC `cat -n` format: leading spaces + line number + tab + content.
// Verified against a real Read tool_result from the project session JSONL
// (e.g. "     3\tfrom fastapi import FastAPI").
const CATN_3 = "     1\t\n     2\t\n     3\tfrom fastapi import FastAPI\n";

describe("ToolBlock — slice-032 Read rendering", () => {
  it("Read done shows path + N lines + elapsed in summary", () => {
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

  it("Read running shows spinner and no N lines", () => {
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

  it("Read with offset preserves real line numbers (not re-numbered from 1)", () => {
    // CC cat -n emits the real file line numbers, so an offset=200 read
    // already carries 200/201/202 in the result text — the renderer must
    // reuse them verbatim, not renumber from 1 or compute from input.offset.
    const offsetResult = "   200\tline A\n   201\tline B\n   202\tline C\n";
    render(
      <ToolBlock
        item={tool({
          toolName: "Read",
          input: { file_path: "/a/big.py", offset: 200 },
          result: offsetResult,
          status: "done",
          elapsedSeconds: 0.5,
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button"));
    const gutters = document.querySelectorAll(".cc-tool__read-gutter");
    expect(gutters.length).toBe(3);
    expect(gutters[0]!.textContent).toBe("200");
    expect(gutters[1]!.textContent).toBe("201");
    expect(gutters[2]!.textContent).toBe("202");
  });

  it("Read detail parses cat -n into gutter | content (no +/- marker)", () => {
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
    const detail = document.querySelector(".cc-tool__detail")!;
    expect(detail.querySelector(".cc-tool__read-line")).toBeTruthy();
    expect(detail.querySelector(".cc-tool__read-gutter")).toBeTruthy();
    // CATN_3's first two rows are blank lines; the "from fastapi" content
    // lives in the 3rd row — assert some content cell carries it.
    const contents = detail.querySelectorAll(".cc-tool__read-content");
    expect(
      Array.from(contents).some((c) => c.textContent?.includes("from fastapi")),
    ).toBe(true);
    // Read is plain content — no diff add/remove markers.
    expect(detail.querySelector('[data-type="add"]')).toBeNull();
    expect(detail.querySelector('[data-type="remove"]')).toBeNull();
  });

  it("Read detail falls back to <pre> when result is not cat -n format", () => {
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
    const detail = document.querySelector(".cc-tool__detail")!;
    expect(detail.querySelector(".cc-tool__bash-out")).toBeTruthy();
    expect(detail.querySelector(".cc-tool__read-line")).toBeNull();
  });

  it("Read detail handles CRLF (Windows) line endings without falling back", () => {
    // CRLF must not force the <pre> fallback — the regex anchors on `$`,
    // which doesn't match `\r`, so CRLF files would silently degrade unless
    // parseCatN normalizes line endings first.
    const crlfResult = "     1\timport os\r\n     2\timport sys\r\n";
    render(
      <ToolBlock
        item={tool({
          toolName: "Read",
          input: { file_path: "/a/win.py" },
          result: crlfResult,
          status: "done",
          elapsedSeconds: 0.2,
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button"));
    const detail = document.querySelector(".cc-tool__detail")!;
    expect(detail.querySelector(".cc-tool__read-line")).toBeTruthy();
    expect(detail.querySelector(".cc-tool__bash-out")).toBeNull();
    // content row carries the code without a trailing CR
    const contents = detail.querySelectorAll(".cc-tool__read-content");
    expect(contents[0]!.textContent).toBe("import os");
  });

  it("Read shows project-relative path with workdir", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Read",
          input: { file_path: "/Users/me/proj/web/x.py" },
          result: CATN_3,
          status: "done",
          elapsedSeconds: 0.2,
        })}
        workdir="/Users/me/proj"
      />,
    );
    expect(screen.getByText("web/x.py")).toBeTruthy();
    expect(screen.queryByText("/Users/me/proj/web/x.py")).toBeNull();
  });
});

describe("ToolBlock — slice-033", () => {
  it("feat 2: Edit done with writeDiff renders cc's REAL file line numbers", () => {
    const wd: WriteDiff = {
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
          input: { file_path: "/a/service.py", old_string: "x", new_string: "y" },
          writeDiff: wd,
          status: "done",
          elapsedSeconds: 0.8,
        })}
      />,
    );
    // feat 2: gutter carries the real file line (360), not fragment-from-1.
    const gutters = document.querySelectorAll(".cc-tool__diff-gutter");
    expect([...gutters].some((g) => g.textContent === "360")).toBeTruthy();
  });

  it("feat 2: Edit done WITHOUT writeDiff falls back to fragment diff", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Edit",
          input: { file_path: "/a/b.ts", old_string: "same\nold\n", new_string: "same\nnew\n" },
          status: "done",
          elapsedSeconds: 0.3,
        })}
      />,
    );
    // stat still computed from the fragment diff (+1 −1)
    expect(screen.getByText("+1")).toBeTruthy();
    expect(screen.getByText("−1")).toBeTruthy();
  });

  it("feat 3: Edit done auto-expands (detail present without a click)", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Edit",
          input: { file_path: "/a/b.ts", old_string: "a", new_string: "b" },
          status: "done",
          elapsedSeconds: 0.3,
        })}
      />,
    );
    expect(document.querySelector(".cc-tool__detail")).toBeTruthy();
  });

  it("feat 3: Edit running stays collapsed (no detail)", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Edit",
          input: { file_path: "/a/b.ts", old_string: "a", new_string: "b" },
          status: "running",
          elapsedSeconds: null,
        })}
      />,
    );
    expect(document.querySelector(".cc-tool__detail")).toBeNull();
  });

  it("feat 3: Bash done does NOT auto-expand (non-diff tool)", () => {
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

  it("feat 4: multi-statement bash command splits one line per statement", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Bash",
          input: { command: 'cd /x; echo "===docs==="; ls' },
          status: "done",
          elapsedSeconds: 0.5,
          result: "ok",
        })}
      />,
    );
    // Bash is non-diff → stays collapsed; click to expand.
    fireEvent.click(screen.getByRole("button"));
    const cmd = document.querySelector(".cc-tool__bash-cmd")!;
    // 3 statements → 3 non-empty lines.
    const lines = cmd.textContent!.split("\n").filter((l) => l.trim() !== "");
    expect(lines.length).toBe(3);
    // 2 separators dimmed (between the 3 statements).
    expect(cmd.querySelectorAll(".cc-tool__bash-sep").length).toBe(2);
  });

  it("feat 4: single-statement bash command is NOT split (renders verbatim)", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Bash",
          input: { command: "echo hello world" },
          status: "done",
          elapsedSeconds: 0.1,
          result: "hello world",
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button"));
    const cmd = document.querySelector(".cc-tool__bash-cmd")!;
    expect(cmd.querySelectorAll(".cc-tool__bash-sep").length).toBe(0);
    expect(cmd.textContent).toBe("echo hello world");
  });
});
