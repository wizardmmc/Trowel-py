/** 验证共同公开后可以在逐轮决定与自动批次之间切换。 */

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Discussion } from "../discussion/domain";
import { DiscussionView } from "../discussion/ui/DiscussionView";

const discussion: Discussion = {
  id: "discussion-1",
  topic: "选择哪种推进方式？",
  workdir: "/repo",
  progression_mode: "user_guided",
  max_rounds: null,
  status: "waiting_user",
  version: 3,
  active_round_number: 1,
  created_at: "2026-08-06T10:00:00Z",
  updated_at: "2026-08-06T10:01:00Z",
  completed_at: null,
  stopped_at: null,
  participants: [
    {
      id: "p1",
      position: 0,
      name: "参与者 1",
      runtime: "codex",
      connection_name: "PRO X20",
      model: "gpt-test",
      effective_model: "gpt-test",
      effort: "high",
      permission_mode: null,
      permission_preset: "read-only",
      memory_enabled: true,
      profile_enabled: true,
      self_enabled: true,
      status: "ready",
    },
    {
      id: "p2",
      position: 1,
      name: "参与者 2",
      runtime: "claude_code",
      connection_name: "GLM-2",
      model: "sonnet",
      effective_model: "glm-5-turbo",
      effort: null,
      permission_mode: "dontAsk",
      permission_preset: null,
      memory_enabled: true,
      profile_enabled: true,
      self_enabled: true,
      status: "ready",
    },
  ],
  messages: [],
  rounds: [],
  handoffs: [],
};

describe("DiscussionView progression", () => {
  it("offers one guided round or an automatic batch starting at one round", async () => {
    const onContinue = vi.fn();
    render(
      <DiscussionView
        discussion={discussion}
        pendingCommand={null}
        error={null}
        onStart={() => {}}
        onContinue={onContinue}
        onFinish={() => {}}
        onStop={() => {}}
        onResume={() => {}}
        onDelete={() => {}}
        onAddMessage={async () => {}}
        onMark={() => {}}
        onHandoff={() => {}}
        onClearError={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "继续 1 轮" }));
    expect(onContinue).toHaveBeenCalledWith("user_guided", null);

    expect(screen.getByRole("combobox", { name: "自动推进轮数" })).toHaveTextContent(
      "再 3 轮",
    );
    await userEvent.click(screen.getByRole("combobox", { name: "自动推进轮数" }));
    expect(screen.getByRole("listbox").closest("[data-side]")).toHaveAttribute(
      "data-side",
      "top",
    );
    await userEvent.click(screen.getByRole("option", { name: "再 1 轮" }));
    fireEvent.click(screen.getByRole("button", { name: "自动推进" }));
    expect(onContinue).toHaveBeenCalledWith("automatic", 1);
  });
});
