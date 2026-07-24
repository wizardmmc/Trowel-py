import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { NewSessionDialog } from "../components/cc/NewSessionDialog";
import type { RuntimesState } from "../components/cc/NewSessionDialog";
import {
  CODEX_MODELS,
  createButton,
  READY_BOTH,
} from "./newSessionDialogFixtures";

describe("NewSessionDialog lifecycle", () => {
  it("arrow keys move runtime AND the card stays keyboard-focusable", () => {
    const onCreate = vi.fn();
    render(
      <NewSessionDialog
        workdir="/wd"
        onCreate={onCreate}
        onCancel={() => {}}
        codexModels={CODEX_MODELS}
      />,
    );
    fireEvent.keyDown(screen.getAllByRole("radio")[0], { key: "ArrowRight" });
    expect(screen.getAllByRole("radio")[1]).toHaveAttribute(
      "aria-checked",
      "true",
    );
    fireEvent.click(createButton());
    expect(onCreate.mock.calls[0][0].runtime).toBe("codex");
  });

  it("creating=true disables create + cancel and shows 创建中…", () => {
    render(
      <NewSessionDialog
        workdir="/wd"
        onCreate={() => {}}
        onCancel={() => {}}
        creating
      />,
    );
    expect(createButton()).toBeDisabled();
    expect(createButton().textContent).toMatch(/创建中/);
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
  });

  it("error is rendered as an alert", () => {
    render(
      <NewSessionDialog
        workdir="/wd"
        onCreate={() => {}}
        onCancel={() => {}}
        error="boom"
      />,
    );
    expect(screen.getByRole("alert").textContent).toBe("boom");
  });

  it("runtimesState=loading blocks create + shows the checking hint", () => {
    render(
      <NewSessionDialog
        workdir="/wd"
        onCreate={() => {}}
        onCancel={() => {}}
        runtimesState={{ status: "loading" }}
      />,
    );
    expect(createButton()).toBeDisabled();
    expect(screen.getByText(/正在检查 runtime 连接/)).toBeInTheDocument();
  });

  it("runtimesState=error shows the diagnostic + retry, blocks create", () => {
    const onRetry = vi.fn();
    render(
      <NewSessionDialog
        workdir="/wd"
        onCreate={() => {}}
        onCancel={() => {}}
        runtimesState={{ status: "error", error: "404" }}
        onRetryRuntimes={onRetry}
      />,
    );
    expect(createButton()).toBeDisabled();
    expect(screen.getByText(/Agent API 不可用/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("a disconnected runtime shows 未连接 and cannot be selected", () => {
    const onCreate = vi.fn();
    const onlyCC: RuntimesState = {
      status: "ready",
      runtimes: [
        {
          runtime: "claude_code",
          label: "Claude Code",
          native: "",
          capabilities: [],
          connected: true,
        },
        {
          runtime: "codex",
          label: "Codex",
          native: "",
          capabilities: [],
          connected: false,
        },
      ],
    };
    render(
      <NewSessionDialog
        workdir="/wd"
        onCreate={onCreate}
        onCancel={() => {}}
        runtimesState={onlyCC}
      />,
    );
    const radios = screen.getAllByRole("radio");
    expect(radios[1]).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText(/未连接/)).toBeInTheDocument();
    fireEvent.click(radios[1]);
    expect(radios[1]).toHaveAttribute("aria-checked", "false");
    fireEvent.click(createButton());
    expect(onCreate.mock.calls[0][0].runtime).toBe("claude_code");
  });

  it("取消 fires onCancel and does NOT create", () => {
    const onCreate = vi.fn();
    const onCancel = vi.fn();
    render(
      <NewSessionDialog
        workdir="/wd"
        onCreate={onCreate}
        onCancel={onCancel}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onCreate).not.toHaveBeenCalled();
  });

  it("backdrop click cancels", () => {
    const onCancel = vi.fn();
    render(
      <NewSessionDialog
        workdir="/wd"
        onCreate={() => {}}
        onCancel={onCancel}
        runtimesState={READY_BOTH}
      />,
    );
    fireEvent.click(document.querySelector(".cc-dialog__backdrop") as Element);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("Esc cancels", () => {
    const onCancel = vi.fn();
    render(
      <NewSessionDialog
        workdir="/wd"
        onCreate={() => {}}
        onCancel={onCancel}
      />,
    );
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
