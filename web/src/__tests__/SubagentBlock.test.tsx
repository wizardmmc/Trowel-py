import { describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { SubagentBlock } from "../components/cc/SubagentBlock";
import type { SubagentState, ToolItem } from "../stores/ccStore";

describe("SubagentBlock (slice-025-a A3)", () => {
  it("renders type + description + last tool + tokens while in progress", () => {
    const sub: SubagentState = {
      status: "progress",
      description: "Count files in directory",
      subagent_type: "general-purpose",
      last_tool_name: "Bash",
      usage: { total_tokens: 1200 },
    };
    render(<SubagentBlock subagent={sub} />);
    expect(screen.getByText(/Agent · general-purpose/)).toBeInTheDocument();
    expect(screen.getByText(/Count files/)).toBeInTheDocument();
    expect(screen.getByText(/last: Bash/)).toBeInTheDocument();
    expect(screen.getByText(/1.2k tok/)).toBeInTheDocument();
    expect(screen.getByLabelText("进行中")).toBeInTheDocument();
  });

  it("shows 'Done' with usage summary when completed", () => {
    render(
      <SubagentBlock
        subagent={{
          status: "completed",
          usage: { total_tokens: 1200, tool_uses: 15, duration_ms: 308000 },
        }}
      />,
    );
    const done = screen.getByText(/Done/);
    expect(done.textContent).toMatch(/15 tool uses/);
    expect(done.textContent).toMatch(/1\.2k tokens/);
    expect(done.textContent).toMatch(/5m 8s/);
    expect(screen.queryByLabelText("进行中")).toBeNull();
  });

  it("omits zero/missing usage fields (GLM backend total_tokens=0)", () => {
    render(
      <SubagentBlock
        subagent={{
          status: "completed",
          usage: { total_tokens: 0, tool_uses: 3, duration_ms: 0 },
        }}
      />,
    );
    const done = screen.getByText(/Done/);
    expect(done.textContent).toMatch(/3 tool uses/);
    expect(done.textContent).not.toMatch(/tokens/);
    expect(done.textContent).not.toMatch(/\d+s/);
  });

  it("hides optional fields when absent", () => {
    render(<SubagentBlock subagent={{ status: "progress" }} />);
    // bare "Agent" name, no type/desc/last/tokens
    expect(screen.getByText(/^Agent$/)).toBeInTheDocument();
    expect(screen.queryByText(/tok/)).toBeNull();
    expect(screen.queryByText(/last:/)).toBeNull();
  });

  it("formats tokens under 1000 as a plain number", () => {
    render(
      <SubagentBlock
        subagent={{ status: "started", usage: { total_tokens: 42 } }}
      />,
    );
    expect(screen.getByText(/42 tok/)).toBeInTheDocument();
  });

  it("renders no token span when usage is missing total_tokens", () => {
    render(<SubagentBlock subagent={{ status: "started", usage: {} }} />);
    expect(screen.queryByText(/tok/)).toBeNull();
  });
});

function makeTool(overrides: Partial<ToolItem> = {}): ToolItem {
  return {
    kind: "tool",
    toolUseId: "x",
    toolName: "Bash",
    input: {},
    status: "running",
    elapsedSeconds: null,
    result: null,
    childTools: [],
    ...overrides,
  };
}

describe("SubagentBlock — childTools children region (slice-025-a 阶段B)", () => {
  it("running with 5 childTools shows latest 4 + '+1 more', hides the oldest", () => {
    const children = [1, 2, 3, 4, 5].map((n) =>
      makeTool({ toolUseId: `c${n}`, input: { command: `echo cmd${n}` }, elapsedSeconds: n }),
    );
    render(
      <SubagentBlock subagent={{ status: "progress" }} childTools={children} />,
    );
    expect(screen.queryAllByText(/cmd5/).length).toBeGreaterThan(0);
    expect(screen.queryAllByText(/cmd2/).length).toBeGreaterThan(0);
    expect(screen.queryAllByText(/cmd1/)).toHaveLength(0);
    expect(screen.getByText(/\+1 more/)).toBeInTheDocument();
  });

  it("completed collapses to latest 1 + '+4 more'", () => {
    const children = [1, 2, 3, 4, 5].map((n) =>
      makeTool({
        toolUseId: `c${n}`,
        input: { command: `echo cmd${n}` },
        status: "done",
        elapsedSeconds: n,
      }),
    );
    render(
      <SubagentBlock subagent={{ status: "completed" }} childTools={children} />,
    );
    expect(screen.queryAllByText(/cmd5/).length).toBeGreaterThan(0);
    expect(screen.queryAllByText(/cmd4/)).toHaveLength(0);
    expect(screen.getByText(/\+4 more/)).toBeInTheDocument();
  });

  it("clicking '+N more' expands to show all children", () => {
    const children = [1, 2, 3, 4, 5].map((n) =>
      makeTool({ toolUseId: `c${n}`, input: { command: `echo cmd${n}` } }),
    );
    render(
      <SubagentBlock subagent={{ status: "progress" }} childTools={children} />,
    );
    fireEvent.click(screen.getByText(/\+1 more/));
    expect(screen.queryAllByText(/cmd1/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/\+.*more/)).toBeNull();
  });

  it("clicking the header toggles auto-height <-> full expand", () => {
    const children = [1, 2, 3, 4, 5].map((n) =>
      makeTool({ toolUseId: `c${n}`, input: { command: `echo cmd${n}` } }),
    );
    render(
      <SubagentBlock subagent={{ status: "progress" }} childTools={children} />,
    );
    expect(screen.queryAllByText(/cmd1/)).toHaveLength(0);
    fireEvent.click(screen.getByText(/^Agent$/));
    expect(screen.queryAllByText(/cmd1/).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByText(/^Agent$/));
    expect(screen.queryAllByText(/cmd1/)).toHaveLength(0);
  });

  it("renders no children region when childTools is absent", () => {
    render(<SubagentBlock subagent={{ status: "progress" }} />);
    expect(screen.queryByText(/more/)).toBeNull();
  });

  it("child renders via ToolBlock — shows tool name + input brief (e.g. Bash command)", () => {
    render(
      <SubagentBlock
        subagent={{ status: "progress" }}
        childTools={[
          makeTool({
            toolUseId: "c1",
            toolName: "Bash",
            input: { command: "printf placeholder > /tmp/x" },
            status: "done",
            elapsedSeconds: 7,
          }),
        ]}
      />,
    );
    expect(screen.getByText(/^Bash$/)).toBeInTheDocument();
    // ToolBlock's BashSummary renders the command brief — not just the tool name
    expect(screen.queryAllByText(/printf.*placeholder/).length).toBeGreaterThan(0);
  });
});
