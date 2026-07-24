import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ToolBlock } from "../components/cc/ToolBlock";
import { tool } from "./toolBlockFixtures";

describe("ToolBlock — command detail", () => {
  it("splits a multi-statement Bash command", () => {
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
    fireEvent.click(screen.getByRole("button"));
    const command = document.querySelector(".cc-tool__bash-cmd");
    const lines =
      command?.textContent
        ?.split("\n")
        .filter((line) => line.trim() !== "") ?? [];
    expect(lines).toHaveLength(3);
    expect(command?.querySelectorAll(".cc-tool__bash-sep")).toHaveLength(2);
  });

  it("shows a failed Codex MCP call with title, duration, error, and call", () => {
    const { container } = render(
      <ToolBlock
        item={tool({
          toolName: "node_repl.js",
          input: {
            server: "node_repl",
            tool: "js",
            arguments: {
              code: 'agent.browsers.getForUrl("http://127.0.0.1:5173/")',
              title: "连接本地 Trowel 页面",
            },
          },
          status: "failed",
          durationMs: 1060,
          nativeStatus: "failed",
          result: "No browser is available",
        })}
      />,
    );

    expect(screen.getByText("node_repl.js")).toBeInTheDocument();
    expect(screen.getByText("连接本地 Trowel 页面")).toBeInTheDocument();
    expect(screen.getByText("1.06s")).toBeInTheDocument();
    expect(screen.getByText("Error")).toBeInTheDocument();
    expect(screen.getByText("No browser is available")).toBeInTheDocument();
    expect(screen.getByText("Call")).toBeInTheDocument();
    expect(container.querySelector(".cc-tool__mcp-call")?.textContent).toContain(
      "agent.browsers.getForUrl",
    );
    expect(screen.queryByText(/^Failed$/)).toBeNull();
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "true");
  });

  it("keeps a successful Codex MCP result collapsed until requested", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "node_repl.js",
          input: {
            server: "node_repl",
            tool: "js",
            arguments: {
              code: "await agent.browsers.list()",
              title: "查看可用浏览器",
            },
          },
          status: "done",
          durationMs: 100,
          nativeStatus: "completed",
          result: '{"content":[{"type":"text","text":"[]"}]}',
        })}
      />,
    );

    expect(screen.getByText("node_repl.js")).toBeInTheDocument();
    expect(screen.getByText("查看可用浏览器")).toBeInTheDocument();
    expect(screen.getByText("0.10s")).toBeInTheDocument();
    expect(screen.queryByText("Result")).toBeNull();
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("Result")).toBeInTheDocument();
    expect(screen.getByText("[]")).toBeInTheDocument();
  });

  it("keeps structured MCP result fields instead of flattening them to text", () => {
    const result = JSON.stringify({
      content: [{ type: "text", text: "summary" }],
      structuredContent: { answer: 42 },
      _meta: null,
    });
    const { container } = render(
      <ToolBlock
        item={tool({
          toolName: "docs.lookup",
          input: {
            server: "docs",
            tool: "lookup",
            arguments: { query: "answer" },
          },
          status: "done",
          result,
        })}
      />,
    );

    fireEvent.click(screen.getByRole("button"));
    expect(container.querySelector(".cc-tool__mcp-value")?.textContent).toBe(
      result,
    );
  });

  it("shows an explicit Error row when a failed MCP call has no error body", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "docs.lookup",
          input: {
            server: "docs",
            tool: "lookup",
            arguments: { query: "missing" },
          },
          status: "failed",
          result: null,
        })}
      />,
    );

    expect(screen.getByText("Error")).toBeInTheDocument();
    expect(screen.getByText("No error details provided")).toBeInTheDocument();
  });

  it("keeps a single-statement Bash command verbatim", () => {
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
    const command = document.querySelector(".cc-tool__bash-cmd");
    expect(command?.querySelectorAll(".cc-tool__bash-sep")).toHaveLength(0);
    expect(command?.textContent).toBe("echo hello world");
  });

  it("renders failed Codex command state and output", () => {
    const { container } = render(
      <ToolBlock
        item={tool({
          toolName: "command",
          input: { command: "make test", cwd: "/repo" },
          status: "failed",
          exitCode: 2,
          durationMs: 120,
          cwd: "/repo",
          nativeStatus: "failed",
          result: "boom",
        })}
      />,
    );
    expect(container.querySelector(".cc-tool__check--failed")).not.toBeNull();
    expect(container.querySelector('[aria-label="失败"]')).not.toBeNull();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("exit 2")).toBeInTheDocument();
    expect(
      container.querySelector(".cc-tool__bash-out")?.textContent,
    ).toContain("boom");
  });

  it("shows Codex command, output, meta and copy actions", () => {
    const { container } = render(
      <ToolBlock
        item={tool({
          toolName: "command",
          input: { command: "rg pattern", cwd: "/repo" },
          status: "done",
          exitCode: 0,
          durationMs: 12,
          cwd: "/repo",
          result: "match.txt:1:hit",
        })}
      />,
    );
    fireEvent.click(container.querySelector(".cc-tool__summary")!);
    expect(
      container.querySelector(".cc-tool__bash-cmd")?.textContent,
    ).toContain("rg pattern");
    expect(
      container.querySelector(".cc-tool__bash-out")?.textContent,
    ).toContain("match.txt");
    expect(container.querySelector(".cc-tool__cmd-meta")?.textContent).toContain(
      "exit 0",
    );
    expect(container.querySelector(".cc-tool__cmd-meta")?.textContent).toContain(
      "12ms",
    );
    expect(
      screen.getByRole("button", { name: "复制命令" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "复制输出" }),
    ).toBeInTheDocument();
  });

  it("renders cwd equal to workdir as dot", () => {
    const { container } = render(
      <ToolBlock
        workdir="/repo"
        item={tool({
          toolName: "command",
          input: {
            command: "pwd",
            cwd: "/repo",
            command_actions: [{ type: "unknown", command: "pwd" }],
          },
          status: "done",
          cwd: "/repo",
          exitCode: 0,
          durationMs: 4,
        })}
      />,
    );
    fireEvent.click(container.querySelector(".cc-tool__summary")!);
    expect(container.querySelector(".cc-tool__cmd-meta")?.textContent).toBe(
      "exit 0 · 4ms · .",
    );
  });

  it("shows the action command for an unknown native action", () => {
    render(
      <ToolBlock
        item={tool({
          toolName: "command",
          input: {
            command: "/bin/zsh -lc 'npm test'",
            command_actions: [{ type: "unknown", command: "npm test" }],
          },
          status: "done",
          result: "ok",
        })}
      />,
    );
    expect(screen.getByText("Ran")).toBeInTheDocument();
    expect(screen.getByText("npm test")).toBeInTheDocument();
  });

  it("keeps the full command on the collapsed button title", () => {
    const full = "/bin/zsh -lc 'rg a-very-long-pattern src'";
    render(
      <ToolBlock
        item={tool({
          toolName: "command",
          input: {
            command: full,
            command_actions: [
              { type: "unknown", command: "rg a-very-long-pattern src" },
            ],
          },
          status: "done",
        })}
      />,
    );
    expect(screen.getByRole("button")).toHaveAttribute("title", full);
    expect(screen.getByRole("button")).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });
});
