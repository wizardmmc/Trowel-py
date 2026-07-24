import { screen } from "@testing-library/react";

import type { AgentModel } from "../api/agent";
import type { RuntimesState } from "../components/cc/NewSessionDialog";

export function createButton(): HTMLElement {
  return screen.getByRole("button", { name: /^创建/ });
}

export const READY_BOTH: RuntimesState = {
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

export const CODEX_MODELS: readonly AgentModel[] = [
  {
    id: "gpt-5.6-sol",
    model: "gpt-5.6-sol",
    display_name: "Sol",
    description: "frontier",
    is_default: true,
    default_effort: "low",
    supported_efforts: [
      { value: "low", description: "light" },
      { value: "ultra", description: "delegates" },
    ],
  },
  {
    id: "gpt-5.6-luna",
    model: "gpt-5.6-luna",
    display_name: "Luna",
    description: "fast",
    is_default: false,
    default_effort: "medium",
    supported_efforts: [
      { value: "low", description: "light" },
      { value: "medium", description: "balanced" },
    ],
  },
];
