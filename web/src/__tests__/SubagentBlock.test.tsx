import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { SubagentBlock } from "../components/cc/SubagentBlock";
import type { SubagentState } from "../stores/ccStore";

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
