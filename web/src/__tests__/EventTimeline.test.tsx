import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { EventTimeline } from "../components/cc/EventTimeline";
import type { TurnItem } from "../stores/ccStore";

describe("EventTimeline", () => {
  it("thinking row shows a summary and expands to the raw text", () => {
    const items: TurnItem[] = [{ kind: "thinking", text: "reasoning here" }];
    render(<EventTimeline items={items} />);
    expect(screen.getByText("思考")).toBeTruthy();
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("reasoning here")).toBeTruthy();
  });

  it("marks Codex reasoning complete once a later item exists in an active turn", () => {
    const items: TurnItem[] = [
      { kind: "thinking", text: "reasoning" },
      { kind: "text", text: "answer" },
    ];
    render(<EventTimeline items={items} runtime="codex" isReplay={false} />);
    expect(screen.getByText("Reasoned")).toBeInTheDocument();
    expect(screen.queryByText("Reasoning")).toBeNull();
  });

  it("keeps the trailing Codex reasoning item active", () => {
    render(<EventTimeline items={[{ kind: "thinking", text: "reasoning" }]} runtime="codex" isReplay={false} />);
    expect(screen.getByText("Reasoning")).toBeInTheDocument();
  });

  it("retrying row surfaces attempt + GLM status + delay", () => {
    const items: TurnItem[] = [
      {
        kind: "retrying",
        attempt: 1,
        maxRetries: 5,
        errorStatus: 529,
        error: "overloaded",
        retryDelayMs: 2000,
      },
    ];
    render(<EventTimeline items={items} />);
    expect(screen.getByText(/重试 1\/5/)).toBeTruthy();
    expect(screen.getByText(/GLM 529/)).toBeTruthy();
    expect(screen.getByText(/2s 后/)).toBeTruthy();
  });

  it("retrying row with attempt=0 shows '重试中' without a number", () => {
    const items: TurnItem[] = [
      {
        kind: "retrying",
        attempt: 0,
        maxRetries: 5,
        errorStatus: 529,
        error: "overloaded",
        retryDelayMs: 2000,
      },
    ];
    render(<EventTimeline items={items} />);
    expect(screen.getByText(/重试中/)).toBeTruthy();
    expect(screen.queryByText(/重试 0/)).toBeNull();
  });

  it("error row shows a retry button only for recoverable subclass", () => {
    const recoverable: TurnItem[] = [
      {
        kind: "error",
        subclass: "error_during_execution",
        errors: ["boom"],
        apiErrorStatus: null,
      },
    ];
    const onRetry = vi.fn();
    const { rerender } = render(<EventTimeline items={recoverable} onRetryLast={onRetry} />);
    expect(screen.getByText("重试上一条")).toBeTruthy();
    fireEvent.click(screen.getByText("重试上一条"));
    expect(onRetry).toHaveBeenCalled();

    const terminal: TurnItem[] = [
      {
        kind: "error",
        subclass: "error_max_turns",
        errors: ["loop"],
        apiErrorStatus: null,
      },
    ];
    rerender(<EventTimeline items={terminal} onRetryLast={onRetry} />);
    expect(screen.queryByText("重试上一条")).toBeNull();
    expect(screen.getByText("error_max_turns")).toBeTruthy();
  });

  it("interrupted row shows the soft-transition guidance", () => {
    const items: TurnItem[] = [{ kind: "interrupted" }];
    render(<EventTimeline items={items} />);
    expect(screen.getByText(/已中断/)).toBeTruthy();
    expect(screen.getByText(/自动接上历史/)).toBeTruthy();
  });

  it("compact_boundary renders a divider", () => {
    const items: TurnItem[] = [{ kind: "compact_boundary" }];
    render(<EventTimeline items={items} />);
    expect(screen.getByText(/自动压缩完成/)).toBeTruthy();
  });

  it("renders text items as an AssistantText markdown block (B2)", () => {
    const items: TurnItem[] = [{ kind: "text", text: "# hello\n\n`code`" }];
    const { container } = render(<EventTimeline items={items} />);
    expect(container.querySelector(".cc-md")).toBeTruthy();
    expect(container.querySelector("h1")?.textContent).toContain("hello");
    expect(container.querySelector("code")).toBeTruthy();
  });

  it("keeps a paragraph break between adjacent text items (no collapse)", () => {
    const items: TurnItem[] = [
      { kind: "text", text: "para1" },
      { kind: "text", text: "para2" },
    ];
    const { container } = render(<EventTimeline items={items} />);
    const ps = container.querySelectorAll(".cc-md p");
    expect(ps).toHaveLength(2);
    expect(ps[0].textContent).toBe("para1");
    expect(ps[1].textContent).toBe("para2");
  });

  it("Agent tool renders SubagentBlock with the childTools region", () => {
    const items: TurnItem[] = [
      {
        kind: "tool",
        toolUseId: "agent-1",
        toolName: "Agent",
        input: {},
        status: "running",
        elapsedSeconds: null,
        result: null,
        childTools: [
          {
            kind: "tool",
            toolUseId: "child-bash",
            toolName: "Bash",
            input: {},
            status: "done",
            elapsedSeconds: 3,
            result: "ok",
            childTools: [],
          },
        ],
      },
    ];
    render(<EventTimeline items={items} />);
    expect(screen.getByText(/Agent/)).toBeInTheDocument();
    expect(screen.getByText(/Bash/)).toBeInTheDocument();
  });

  it("hides TaskCreate/TaskUpdate/TodoWrite tools", () => {
    const items: TurnItem[] = [
      { kind: "tool", toolUseId: "1", toolName: "TaskCreate", input: {}, status: "done", elapsedSeconds: 1, result: "ok", childTools: [] },
      { kind: "tool", toolUseId: "2", toolName: "TaskUpdate", input: {}, status: "done", elapsedSeconds: 1, result: "ok", childTools: [] },
      { kind: "tool", toolUseId: "3", toolName: "TodoWrite", input: {}, status: "done", elapsedSeconds: 1, result: "ok", childTools: [] },
    ];
    const { container } = render(<EventTimeline items={items} />);
    expect(container.querySelectorAll(".cc-tool")).toHaveLength(0);
  });

  it("still renders non-task tools (Bash) alongside hidden task tools", () => {
    const items: TurnItem[] = [
      { kind: "tool", toolUseId: "1", toolName: "TaskCreate", input: {}, status: "done", elapsedSeconds: 1, result: "ok", childTools: [] },
      { kind: "tool", toolUseId: "2", toolName: "Bash", input: { command: "ls" }, status: "done", elapsedSeconds: 1, result: "ok", childTools: [] },
    ];
    const { container } = render(<EventTimeline items={items} />);
    expect(container.querySelectorAll(".cc-tool")).toHaveLength(1);
    expect(screen.getByText("Bash")).toBeInTheDocument();
  });

  it("keeps every tool summary but auto-opens only the newest 4 diff details beyond the extreme threshold", () => {
    const items: TurnItem[] = Array.from({ length: 34 }, (_, index) => ({
      kind: "tool" as const,
      toolUseId: `patch-${index}`,
      toolName: "apply_patch",
      input: { paths: [`/repo/file-${index}.ts`] },
      status: "done" as const,
      elapsedSeconds: 0.1,
      result: "ok",
      childTools: [],
      writeDiff: {
        type: "update" as const,
        hunks: [
          {
            oldStart: 1,
            oldLines: 0,
            newStart: 1,
            newLines: 1,
            lines: [`+line ${index}`],
          },
        ],
      },
    }));

    const { container } = render(
      <EventTimeline items={items} workdir="/repo" allowExtremeCompaction />,
    );

    expect(container.querySelectorAll(".cc-tool")).toHaveLength(34);
    expect(
      container.querySelectorAll('.cc-tool__summary[aria-expanded="true"]'),
    ).toHaveLength(4);
    expect(container.querySelectorAll(".cc-tool__diff-line")).toHaveLength(4);

    fireEvent.click(screen.getByRole("button", { name: /file-0\.ts/ }));
    expect(container.querySelectorAll(".cc-tool__diff-line")).toHaveLength(5);
  });

  it("mounts old Markdown as readable lightweight text and restores rich rendering on demand", () => {
    const items: TurnItem[] = [];
    for (let index = 0; index < 34; index += 1) {
      items.push({ kind: "text", text: `# old text ${index}` });
      items.push({
        kind: "tool",
        toolUseId: `patch-${index}`,
        toolName: "apply_patch",
        input: { paths: [`/repo/file-${index}.ts`] },
        status: "done",
        elapsedSeconds: 0.1,
        result: "ok",
        childTools: [],
        writeDiff: {
          type: "update",
          hunks: [{
            oldStart: 1,
            oldLines: 0,
            newStart: 1,
            newLines: 1,
            lines: [`+line ${index}`],
          }],
        },
      });
    }

    const { container, rerender } = render(
      <EventTimeline items={items} workdir="/repo" allowExtremeCompaction />,
    );

    expect(container.querySelectorAll(".cc-tool")).toHaveLength(34);
    expect(screen.getByText("# old text 0")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "old text 33" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "old text 0" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "渲染较早 Markdown" }));
    expect(screen.getByRole("heading", { name: "old text 0" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "较早文字使用轻量显示" }),
    ).toBeInTheDocument();

    rerender(
      <EventTimeline
        items={[...items, {
          kind: "tool",
          toolUseId: "patch-later",
          toolName: "apply_patch",
          input: { paths: ["/repo/later.ts"] },
          status: "done",
          elapsedSeconds: 0.1,
          result: "ok",
          childTools: [],
          writeDiff: {
            type: "update",
            hunks: [{
              oldStart: 1,
              oldLines: 0,
              newStart: 1,
              newLines: 1,
              lines: ["+later"],
            }],
          },
        }]}
        workdir="/repo"
        allowExtremeCompaction
      />,
    );
    expect(screen.getByRole("heading", { name: "old text 0" })).toBeInTheDocument();
  });

  it("does not compact a growing turn until bottom-following allows it", async () => {
    const buildItems = (count: number): TurnItem[] =>
      Array.from({ length: count }, (_, index) => ({
        kind: "tool" as const,
        toolUseId: `patch-${index}`,
        toolName: "apply_patch",
        input: { paths: [`/repo/file-${index}.ts`] },
        status: "done" as const,
        elapsedSeconds: 0.1,
        result: "ok",
        childTools: [],
        writeDiff: {
          type: "update" as const,
          hunks: [{
            oldStart: 1,
            oldLines: 0,
            newStart: 1,
            newLines: 1,
            lines: [`+line ${index}`],
          }],
        },
      }));
    const { container, rerender } = render(
      <EventTimeline items={buildItems(32)} allowExtremeCompaction={false} />,
    );

    rerender(
      <EventTimeline items={buildItems(34)} allowExtremeCompaction={false} />,
    );
    expect(
      container.querySelectorAll('.cc-tool__summary[aria-expanded="true"]'),
    ).toHaveLength(32);
    expect(screen.getByRole("button", { name: /file-0\.ts/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByRole("button", { name: /file-33\.ts/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );

    rerender(
      <EventTimeline items={buildItems(34)} allowExtremeCompaction />,
    );
    await waitFor(() =>
      expect(
        container.querySelectorAll('.cc-tool__summary[aria-expanded="true"]'),
      ).toHaveLength(4),
    );
    expect(container.querySelectorAll(".cc-tool")).toHaveLength(34);
  });

  it("groups contiguous Codex exploration commands without merging child buttons", () => {
    const items: TurnItem[] = [
      { kind: "tool", toolUseId: "1", toolName: "command", input: { command: "ls", command_actions: [{ type: "listFiles", command: "ls", path: null }] }, status: "done", elapsedSeconds: null, result: "a", childTools: [] },
      { kind: "tool", toolUseId: "2", toolName: "command", input: { command: "cat a", command_actions: [{ type: "read", command: "cat a", name: "a", path: "/repo/a" }] }, status: "done", elapsedSeconds: null, result: "x", childTools: [] },
      { kind: "tool", toolUseId: "3", toolName: "command", input: { command: "rg x", command_actions: [{ type: "search", command: "rg x", query: "x", path: "." }] }, status: "running", elapsedSeconds: null, result: null, childTools: [] },
    ];
    const { container } = render(<EventTimeline items={items} runtime="codex" workdir="/repo" />);
    expect(container.querySelectorAll(".cc-exploration")).toHaveLength(1);
    expect(screen.getByText("Exploring · 3 calls")).toBeInTheDocument();
    expect(screen.getByText("List")).toBeInTheDocument();
    expect(screen.getByText("Read")).toBeInTheDocument();
    expect(screen.getByText("Search")).toBeInTheDocument();
    expect(container.querySelectorAll(".cc-exploration .cc-tool__summary")).toHaveLength(3);
  });

  it("renders one call status above three status-free Read actions", () => {
    const items: TurnItem[] = [
      {
        kind: "tool",
        toolUseId: "read-3",
        toolName: "command",
        input: {
          command: "sed files",
          command_actions: [
            { type: "read", command: "cat a", name: "a", path: "/repo/a.ts" },
            { type: "read", command: "cat b", name: "b", path: "/repo/b.ts" },
            { type: "read", command: "cat c", name: "c", path: "/repo/c.ts" },
          ],
        },
        status: "done",
        elapsedSeconds: 0.1,
        result: null,
        childTools: [],
      },
    ];

    const { container } = render(
      <EventTimeline items={items} runtime="codex" workdir="/repo" />,
    );
    expect(screen.getByText("Explored · 1 call")).toBeInTheDocument();
    expect(screen.getByText("3 actions")).toBeInTheDocument();
    expect(screen.getByText("Read 3 files")).toBeInTheDocument();
    expect(container.querySelectorAll(".cc-exploration__items .cc-tool__codex-dot")).toHaveLength(1);
    expect(container.querySelectorAll(".cc-tool__action-row")).toHaveLength(3);
    expect(container.querySelectorAll(".cc-tool__action-row .cc-tool__codex-dot")).toHaveLength(0);
    expect(container.querySelector(".cc-tool__bash-cmd")).toBeNull();
    expect(screen.getByText("a.ts")).toBeInTheDocument();
    expect(screen.getByText("b.ts")).toBeInTheDocument();
    expect(screen.getByText("c.ts")).toBeInTheDocument();
  });

  it("summarizes mixed exploration actions on the parent call", () => {
    const items: TurnItem[] = [
      {
        kind: "tool",
        toolUseId: "mixed",
        toolName: "command",
        input: {
          command: "inspect repo",
          command_actions: [
            { type: "search", command: "rg browser", query: "browser", path: "." },
            { type: "read", command: "cat a", name: "a", path: "/repo/a.ts" },
            { type: "read", command: "cat b", name: "b", path: "/repo/b.ts" },
          ],
        },
        status: "done",
        elapsedSeconds: 0.2,
        result: null,
        childTools: [],
      },
    ];

    render(<EventTimeline items={items} runtime="codex" workdir="/repo" />);
    expect(screen.getByText("Explore 3 actions")).toBeInTheDocument();
    expect(screen.getByText("2 Read · 1 Search")).toBeInTheDocument();
  });

  it("text, reasoning, and unknown commands each break Codex exploration clusters", () => {
    const explore = (id: string): TurnItem => ({ kind: "tool", toolUseId: id, toolName: "command", input: { command: "ls", command_actions: [{ type: "listFiles", command: "ls", path: null }] }, status: "done", elapsedSeconds: null, result: null, childTools: [] });
    const run: TurnItem = { kind: "tool", toolUseId: "run", toolName: "command", input: { command: "npm test", command_actions: [{ type: "unknown", command: "npm test" }] }, status: "done", elapsedSeconds: null, result: null, childTools: [] };
    const items: TurnItem[] = [explore("1"), { kind: "text", text: "middle" }, explore("2"), { kind: "thinking", text: "why" }, explore("3"), run, explore("4")];
    const { container } = render(<EventTimeline items={items} runtime="codex" />);
    expect(container.querySelectorAll(".cc-exploration")).toHaveLength(4);
    expect(screen.getByText("Ran")).toBeInTheDocument();
  });

  it("reports failed exploration children on the cluster and child row", () => {
    const items: TurnItem[] = [
      { kind: "tool", toolUseId: "1", toolName: "command", input: { command: "cat missing", command_actions: [{ type: "read", command: "cat missing", name: "missing", path: "/repo/missing" }] }, status: "failed", elapsedSeconds: null, result: "not found", exitCode: 1, childTools: [] },
    ];
    const { container } = render(<EventTimeline items={items} runtime="codex" />);
    expect(screen.getByText("Explored · 1 call · 1 failed")).toBeInTheDocument();
    expect(container.querySelector('[aria-label="失败"]')).not.toBeNull();
  });

  it("does not group command tools for CC", () => {
    const items: TurnItem[] = [
      { kind: "tool", toolUseId: "1", toolName: "command", input: { command: "ls", command_actions: [{ type: "listFiles", command: "ls", path: null }] }, status: "done", elapsedSeconds: null, result: null, childTools: [] },
    ];
    const { container } = render(<EventTimeline items={items} runtime="claude_code" />);
    expect(container.querySelector(".cc-exploration")).toBeNull();
  });
});
