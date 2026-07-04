import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ToolBlock } from "../components/cc/ToolBlock";
import type { ToolItem } from "../stores/ccStore";

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

describe("ToolBlock", () => {
  it("Write tool shows the path in the summary", () => {
    render(<ToolBlock item={tool({ toolName: "Write", input: { file_path: "/a/b.txt" } })} />);
    expect(screen.getByText("/a/b.txt")).toBeTruthy();
  });

  it("Bash tool shows the command in the summary", () => {
    render(
      <ToolBlock
        item={tool({ toolName: "Bash", input: { command: "echo hi" } })}
      />,
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
    // collapsed by default — summary shows the tool name
    expect(screen.getAllByText("Grep").length).toBeGreaterThan(0);
    // expand to see JSON detail
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText(/"pattern": "foo"/)).toBeTruthy();
  });

  it("done tool shows a check + elapsed time", () => {
    render(
      <ToolBlock
        item={tool({ status: "done", elapsedSeconds: 0.8, result: "ok" })}
      />,
    );
    expect(screen.getByText("0.8s")).toBeTruthy();
    expect(screen.getByLabelText("完成")).toBeTruthy();
  });

  it("running tool with elapsed shows elapsed but no check", () => {
    render(<ToolBlock item={tool({ status: "running", elapsedSeconds: 1.2 })} />);
    expect(screen.getByText("1.2s")).toBeTruthy();
    expect(screen.queryByLabelText("完成")).toBeNull();
  });

  it("Write expanded shows path + result preview", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "Write",
          input: { file_path: "/x/y.txt" },
          status: "done",
          result: "wrote 1 file",
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getAllByText("/x/y.txt").length).toBeGreaterThan(0);
    expect(screen.getByText("wrote 1 file")).toBeTruthy();
  });
});
