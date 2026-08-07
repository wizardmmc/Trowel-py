/** 验证研讨交接先接收用户指令，再按普通 Agent 会话配置启动。 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { AgentConnectionOption } from "../agent/application";
import type { Discussion } from "../discussion/domain";
import { HandoffDialog } from "../discussion/ui/HandoffDialog";

const connections: readonly AgentConnectionOption[] = [
  {
    id: "codex-main",
    name: "PRO X20",
    runtime: "codex",
    kind: "codex_custom",
    identity_version: 1,
    available: true,
    disabled_reason: null,
    last_session_choice: { model: "gpt-test", effort: "high" },
    models: [
      {
        id: "gpt-test",
        display_name: "GPT Test",
        available: true,
        disabled_reason: null,
        efforts: ["high"],
        default_effort: "high",
      },
    ],
  },
];

const discussion: Discussion = {
  id: "discussion-handoff",
  topic: "如何继续实现？",
  workdir: "/repo",
  progression_mode: "user_guided",
  max_rounds: null,
  status: "waiting_user",
  version: 2,
  active_round_number: 1,
  created_at: "2026-08-07T10:00:00Z",
  updated_at: "2026-08-07T10:01:00Z",
  completed_at: null,
  stopped_at: null,
  participants: [],
  messages: [],
  rounds: [],
  handoffs: [],
};

describe("HandoffDialog", () => {
  it("requires an explicit instruction and submits it separately from config", () => {
    const onSubmit = vi.fn();
    render(
      <HandoffDialog
        discussion={discussion}
        connections={connections}
        pending={false}
        onChooseWorkdir={() => {}}
        onSubmit={onSubmit}
        onCancel={() => {}}
      />,
    );

    const submit = screen.getByRole("button", { name: "创建并发送指令" });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText("给 Agent 的指令"), {
      target: { value: "先核对代码，再完成实现" },
    });
    expect(submit).toBeEnabled();

    fireEvent.click(submit);

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        runtime: "codex",
        connection_id: "codex-main",
        model: "gpt-test",
        permission_preset: "follow",
        workdir: "/repo",
      }),
      "先核对代码，再完成实现",
    );
  });
});
