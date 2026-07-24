import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { WorkflowTree } from "../components/cc/WorkflowTree";
import type { WorkflowItem } from "../stores/ccReducer";

function completed(): WorkflowItem {
  return {
    kind: "workflow",
    runId: "wf_1",
    taskId: "task_1",
    name: "baseline",
    args: "test question text",
    status: "completed",
    agentCount: 2,
    doneCount: 2,
    totalTokens: 1234,
    totalToolCalls: 3,
    durationMs: 60000,
    phases: [
      { title: "Scope", detail: "decompose question" },
      { title: "Run", detail: "parallel agents" },
    ],
    agents: [
      {
        agent_id: "a1",
        label: "scope",
        phase_index: 1,
        phase_title: "Scope",
        model: "glm-5.1",
        state: "done",
        tokens: 100,
        tool_calls: 1,
        last_tool_name: "Bash",
        duration_ms: 1000,
        prompt_preview: "decompose prompt",
        result_preview: "5 angles",
      },
      {
        agent_id: "a2",
        label: "run:a",
        phase_index: 2,
        phase_title: "Run",
        model: "glm-5.1",
        state: "done",
        tokens: 200,
        tool_calls: 2,
        last_tool_name: "WebSearch",
        duration_ms: 2000,
        prompt_preview: null,
        result_preview: null,
      },
    ],
    error: null,
  };
}

describe("WorkflowTree", () => {
  it("renders the workflow name, status, progress, and stats", () => {
    render(<WorkflowTree workflow={completed()} />);
    expect(screen.getByText("Workflow · baseline")).toBeTruthy();
    expect(screen.getByText(/✓ completed/)).toBeTruthy();
    expect(screen.getByText("2/2")).toBeTruthy();
    const stats = document.querySelector(".cc-wf-stats");
    expect(stats?.textContent ?? "").toMatch(/1\.2k/);
    expect(stats?.textContent ?? "").toMatch(/3/);
    expect(stats?.textContent ?? "").toMatch(/tools/);
  });

  it("renders each phase title + detail", () => {
    render(<WorkflowTree workflow={completed()} />);
    expect(screen.getByText("Scope")).toBeTruthy();
    expect(screen.getByText("decompose question")).toBeTruthy();
    expect(screen.getByText("Run")).toBeTruthy();
  });

  it("renders each agent label + tokens under its phase", () => {
    render(<WorkflowTree workflow={completed()} />);
    expect(screen.getByText("scope")).toBeTruthy();
    expect(screen.getByText("run:a")).toBeTruthy();
    expect(screen.getByText("100 tok")).toBeTruthy();
    expect(screen.getByText("200 tok")).toBeTruthy();
  });

  it("shows the running badge + spin ring while in flight", () => {
    const wf = { ...completed(), status: "running" as const, doneCount: 1 };
    render(<WorkflowTree workflow={wf} />);
    expect(screen.getByText("running")).toBeTruthy();
    expect(screen.getByText("1/2")).toBeTruthy();
  });

  it("surfaces a red error block on killed (C-5)", () => {
    const wf = {
      ...completed(),
      status: "killed" as const,
      error: "Error: Workflow aborted at S (cli.js)",
    };
    render(<WorkflowTree workflow={wf} />);
    expect(screen.getByText("Workflow aborted")).toBeTruthy();
    expect(screen.getByText(/Workflow aborted at S/)).toBeTruthy();
  });

  it("expands an agent head to show its prompt/result previews", () => {
    render(<WorkflowTree workflow={completed()} />);
    expect(screen.queryByText("decompose prompt")).toBeNull();
    const scopeHead = screen.getByText("scope").closest('[role="button"]');
    expect(scopeHead).not.toBeNull();
    fireEvent.click(scopeHead!);
    expect(screen.getByText(/decompose prompt/)).toBeTruthy();
    expect(screen.getByText("5 angles")).toBeTruthy();
  });

  it("collapses the whole tree when the workflow header is clicked", () => {
    render(<WorkflowTree workflow={completed()} />);
    expect(screen.getByText("Scope")).toBeTruthy();
    fireEvent.click(screen.getByText("Workflow · baseline"));
    expect(screen.queryByText("Scope")).toBeNull();
  });
});
