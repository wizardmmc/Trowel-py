import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { NewSessionDialog } from "../components/cc/NewSessionDialog";
import type { RuntimesState } from "../components/cc/NewSessionDialog";

/** The primary create button — its label changes with the chosen runtime. */
function createButton(): HTMLElement {
  return screen.getByRole("button", { name: /^创建/ });
}

const READY_BOTH: RuntimesState = {
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
      connected: true,
    },
  ],
};

describe("NewSessionDialog (slice-072)", () => {
  it("defaults to runtime=claude_code + both switches ON + empty model/effort/permission", () => {
    render(
      <NewSessionDialog workdir="/wd" onCreate={() => {}} onCancel={() => {}} />,
    );
    const radios = screen.getAllByRole("radio");
    expect(radios[0]).toHaveAttribute("aria-checked", "true"); // claude_code
    expect(radios[1]).toHaveAttribute("aria-checked", "false");
    const switches = screen.getAllByRole("switch");
    expect(switches[0]).toHaveAttribute("aria-checked", "true");
    expect(switches[1]).toHaveAttribute("aria-checked", "true");
  });

  it("create fires onCreate with runtime + M/P + model/effort/permission", () => {
    const onCreate = vi.fn();
    render(
      <NewSessionDialog workdir="/wd" onCreate={onCreate} onCancel={() => {}} />,
    );
    // pick Codex + a non-default model/effort/permission
    fireEvent.click(screen.getAllByRole("radio")[1]);
    fireEvent.click(screen.getByText("gpt-5.6-sol"));
    fireEvent.click(screen.getByText("high"));
    fireEvent.click(screen.getByText("workspace-write"));
    fireEvent.click(screen.getAllByRole("switch")[0]); // Memory off
    fireEvent.click(createButton());
    expect(onCreate).toHaveBeenCalledWith({
      runtime: "codex",
      memory_enabled: false,
      profile_enabled: true,
      model: "gpt-5.6-sol",
      effort: "high",
      permission_mode: "", // codex → permission_mode empty
      sandbox: "workspace-write",
    });
  });

  it("switching runtime resets model/effort/permission (no leak across runtimes)", () => {
    const onCreate = vi.fn();
    render(
      <NewSessionDialog workdir="/wd" onCreate={onCreate} onCancel={() => {}} />,
    );
    fireEvent.click(screen.getAllByRole("radio")[1]); // → Codex
    fireEvent.click(screen.getByText("gpt-5.6-sol")); // pick Codex model
    fireEvent.click(screen.getAllByRole("radio")[0]); // back to CC
    fireEvent.click(createButton());
    const config = onCreate.mock.calls[0][0];
    expect(config.model).toBe(""); // reset — Codex pick did not leak into CC
    expect(config.runtime).toBe("claude_code");
  });

  it("CC model options come from the ccModels prop (/api/cc/models)", () => {
    const onCreate = vi.fn();
    render(
      <NewSessionDialog
        workdir="/wd"
        onCreate={onCreate}
        onCancel={() => {}}
        ccModels={[
          {
            value: "opus",
            label: "opus",
            real_model: "glm-opus",
            description: "",
          },
          {
            value: "sonnet",
            label: "sonnet",
            real_model: "glm-sonnet",
            description: "",
          },
        ]}
      />,
    );
    fireEvent.click(screen.getByText("opus"));
    fireEvent.click(createButton());
    expect(onCreate.mock.calls[0][0].model).toBe("opus");
    expect(onCreate.mock.calls[0][0].runtime).toBe("claude_code");
  });

  it("arrow keys move runtime AND the card stays keyboard-focusable", () => {
    const onCreate = vi.fn();
    render(
      <NewSessionDialog workdir="/wd" onCreate={onCreate} onCancel={() => {}} />,
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

  it("a disconnected runtime shows 未连接 and cannot be selected (review P1-1)", () => {
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
    // clicking the disconnected card does NOT select it
    fireEvent.click(radios[1]);
    expect(radios[1]).toHaveAttribute("aria-checked", "false");
    // claude_code is still selectable + create works
    fireEvent.click(createButton());
    expect(onCreate.mock.calls[0][0].runtime).toBe("claude_code");
  });

  it("取消 fires onCancel and does NOT create", () => {
    const onCreate = vi.fn();
    const onCancel = vi.fn();
    render(
      <NewSessionDialog workdir="/wd" onCreate={onCreate} onCancel={onCancel} />,
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
