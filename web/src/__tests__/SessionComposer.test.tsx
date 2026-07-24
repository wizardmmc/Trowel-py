import { act, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AgentSession } from "../api/agent";
import { SessionComposer } from "../components/cc/SessionComposer";
import { createNewSessionState } from "../stores/ccStore/sessionState";

const probe = vi.hoisted(() => ({
  props: null as Record<string, unknown> | null,
}));

vi.mock("../components/cc/Composer", () => ({
  Composer: (props: Record<string, unknown>) => {
    probe.props = props;
    return <div data-testid="composer-probe" />;
  },
}));

function session(runtime: "claude_code" | "codex"): AgentSession {
  return {
    session_id: "s1",
    runtime,
    native_session_id: null,
    workdir: "/repo",
    model: runtime === "codex" ? "gpt-model" : "real-model",
    effort: "high",
    permission: null,
    memory_enabled: true,
    profile_enabled: true,
    capabilities: ["tools"],
    name: "repo",
    connected: true,
    running: false,
  };
}

function baseProps(runtime: "claude_code" | "codex") {
  return {
    active: createNewSessionState(session(runtime), {
      workdir: "/repo",
      runtime,
      effort: "high",
    }),
    activeSid: "s1",
    streaming: false,
    slashItems: [],
    ccModels: [
      {
        value: "alias",
        label: "Alias",
        real_model: "real-model",
        description: "",
        is_default: true,
      },
    ],
    codexModels: [
      {
        id: "gpt",
        model: "gpt-model",
        display_name: "GPT",
        description: "",
        is_default: true,
        default_effort: "medium",
        supported_efforts: [
          { value: "medium", description: "Medium" },
          { value: "high", description: "High" },
        ],
      },
    ],
    codexCatalogError: null,
    onRetryCodexCatalog: vi.fn(),
    onSend: vi.fn(),
    onInterrupt: vi.fn(),
    onUpdateSettings: vi.fn(),
    onRequestModelPicker: vi.fn(),
    onRequestEffortPicker: vi.fn(),
  };
}

describe("SessionComposer", () => {
  it("maps Claude Code model changes to slash commands", () => {
    const props = baseProps("claude_code");
    render(<SessionComposer {...props} />);

    expect(probe.props?.currentModelAlias).toBe("alias");
    act(() => {
      (probe.props?.onPickModel as (value: string) => void)("sonnet");
    });
    expect(props.onSend).toHaveBeenCalledWith("/model sonnet");
    expect(props.onUpdateSettings).not.toHaveBeenCalled();
  });

  it("maps Codex model changes to settings with a supported effort", () => {
    const props = baseProps("codex");
    render(<SessionComposer {...props} />);

    act(() => {
      (probe.props?.onPickModel as (value: string) => void)("gpt");
    });
    expect(props.onUpdateSettings).toHaveBeenCalledWith("gpt", "high");
    expect(props.onSend).not.toHaveBeenCalled();
  });
});
