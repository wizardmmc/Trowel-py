/** 验证参与者草稿由用户逐个创建，达到八位时仍可编辑、提交和定点删除。 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { AgentConnectionOption } from "../agent/application";
import type { DiscussionSessionConfiguration } from "../discussion/domain";
import { NewDiscussionDialog } from "../discussion/ui/NewDiscussionDialog";

const connections: readonly AgentConnectionOption[] = [
  {
    id: "codex-main",
    name: "Codex 主连接",
    runtime: "codex",
    kind: "codex_official",
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

const configurations: readonly DiscussionSessionConfiguration[] = [
  {
    id: "configuration-codex-main",
    name: "Codex 主配置",
    runtime: "codex",
    connection_id: "codex-main",
    model: "gpt-test",
    effort: "high",
    availability: "available",
    disabled_reason: null,
  },
];

const claudeConnections: readonly AgentConnectionOption[] = [
  {
    id: "claude-glm-2",
    name: "GLM-2",
    runtime: "claude_code",
    kind: "claude_compatible",
    identity_version: 2,
    available: true,
    disabled_reason: null,
    last_session_choice: { model: "opus", effort: "max" },
    models: [
      {
        id: "opus",
        display_name: "opus",
        available: true,
        disabled_reason: null,
        efforts: [],
        default_effort: null,
      },
      {
        id: "sonnet",
        display_name: "sonnet",
        available: true,
        disabled_reason: null,
        efforts: [],
        default_effort: null,
      },
    ],
  },
];

async function createParticipant(number: number): Promise<void> {
  fireEvent.click(screen.getByRole("button", { name: "创建参与者" }));
  expect(screen.getByDisplayValue(`参与者 ${number}`)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "保存参与者" }));
  const countLabel = number === 1
    ? "1 位 · 还需 1 位"
    : `${number} 位 · 同轮共同公开`;
  await screen.findByText(countLabel);
}

describe("NewDiscussionDialog", () => {
  it("starts empty and requires two explicitly configured participants", async () => {
    render(
      <NewDiscussionDialog
        connections={connections}
        sessionConfigurations={configurations}
        loading={false}
        creating={false}
        initialWorkdir="/repo"
        onChooseWorkdir={() => {}}
        onCreate={() => {}}
        onCancel={() => {}}
      />,
    );

    expect(await screen.findByText("0 位 · 至少创建 2 位")).toBeInTheDocument();
    expect(screen.getByText("先创建至少两位参与者")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^编辑 参与者/ })).toBeNull();
    expect(
      screen.getByRole("button", { name: "还需创建 2 位参与者" }),
    ).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "创建参与者" }));
    fireEvent.click(screen.getByRole("button", { name: "保存参与者" }));
    expect(await screen.findByText("1 位 · 还需 1 位")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "还需创建 1 位参与者" }),
    ).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "创建参与者" }));
    fireEvent.click(screen.getByRole("button", { name: "保存参与者" }));
    expect(await screen.findByText("2 位 · 同轮共同公开")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "创建并开始第 1 轮" }),
    ).toBeEnabled();
  });

  it("creates eight independent drafts and only removes the selected one", async () => {
    const onCreate = vi.fn();
    render(
      <NewDiscussionDialog
        connections={connections}
        sessionConfigurations={configurations}
        loading={false}
        creating={false}
        initialWorkdir="/repo"
        onChooseWorkdir={() => {}}
        onCreate={onCreate}
        onCancel={() => {}}
      />,
    );

    await screen.findByText("0 位 · 至少创建 2 位");
    for (let count = 1; count <= 8; count += 1) {
      await createParticipant(count);
    }

    fireEvent.click(screen.getByRole("button", { name: "编辑 参与者 1" }));
    fireEvent.change(screen.getByDisplayValue("参与者 1"), {
      target: { value: "架构评审" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存参与者" }));
    expect(screen.getByText(/架构评审 · Codex 主连接/)).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("需要多个模型独立判断什么问题？"), {
      target: { value: "哪种实现更稳妥？" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建并开始第 1 轮" }));
    await waitFor(() => expect(onCreate).toHaveBeenCalledOnce());
    const submitted = onCreate.mock.calls[0][0];
    expect(submitted.participants).toHaveLength(8);
    expect(submitted.participants[0].name).toBe("架构评审");
    expect(submitted.participants[7]).toMatchObject({
      name: "参与者 8",
      session_configuration_id: "configuration-codex-main",
      permission_mode: null,
      permission_preset: "read-only",
      memory_enabled: true,
      profile_enabled: true,
      self_enabled: true,
    });

    fireEvent.click(screen.getByRole("button", { name: "删除 参与者 3" }));
    expect(screen.getByText("7 位 · 同轮共同公开")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^编辑 参与者/ })).toHaveLength(7);
    expect(screen.queryByRole("button", { name: "编辑 参与者 8" })).toBeNull();
    expect(screen.getByRole("button", { name: "编辑 参与者 7" })).toBeInTheDocument();
  });

  it("renumbers generated participant names after deleting the middle draft", async () => {
    render(
      <NewDiscussionDialog
        connections={connections}
        sessionConfigurations={configurations}
        loading={false}
        creating={false}
        initialWorkdir="/repo"
        onChooseWorkdir={() => {}}
        onCreate={() => {}}
        onCancel={() => {}}
      />,
    );

    await screen.findByText("0 位 · 至少创建 2 位");
    for (let count = 1; count <= 3; count += 1) {
      await createParticipant(count);
    }
    fireEvent.click(screen.getByRole("button", { name: "删除 参与者 2" }));

    const second = screen.getByRole("button", { name: "编辑 参与者 2" });
    expect(second).not.toHaveTextContent("参与者 3");
    expect(screen.queryByRole("button", { name: "编辑 参与者 3" })).toBeNull();
  });

  it("accepts an Agent-valid inline configuration and keeps editor errors local", async () => {
    const onCreate = vi.fn();
    render(
      <NewDiscussionDialog
        connections={connections}
        sessionConfigurations={[]}
        loading={false}
        creating={false}
        initialWorkdir="/repo"
        onChooseWorkdir={() => {}}
        onCreate={onCreate}
        onCancel={() => {}}
      />,
    );

    await screen.findByText("0 位 · 至少创建 2 位");
    fireEvent.click(screen.getByRole("button", { name: "创建参与者" }));
    expect(screen.queryByText(/没有对应的已保存会话配置/)).toBeNull();
    fireEvent.change(screen.getByDisplayValue("参与者 1"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存参与者" }));
    expect(screen.getByRole("alert")).toHaveTextContent("参与者名称不能为空");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByText("参与者名称不能为空")).toBeNull();

    await createParticipant(1);
    await createParticipant(2);

    fireEvent.change(screen.getByPlaceholderText("需要多个模型独立判断什么问题？"), {
      target: { value: "直接组合能否创建？" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建并开始第 1 轮" }));
    await waitFor(() => expect(onCreate).toHaveBeenCalledOnce());
    expect(onCreate.mock.calls[0][0].participants[0]).toMatchObject({
      connection_id: "codex-main",
      model: "gpt-test",
      effort: "high",
    });
    expect(onCreate.mock.calls[0][0].participants[0]).not.toHaveProperty(
      "session_configuration_id",
    );
  });

  it("restores and submits Claude effort independently of the role model catalog", async () => {
    const onCreate = vi.fn();
    render(
      <NewDiscussionDialog
        connections={claudeConnections}
        sessionConfigurations={[]}
        loading={false}
        creating={false}
        initialWorkdir="/repo"
        onChooseWorkdir={() => {}}
        onCreate={onCreate}
        onCancel={() => {}}
      />,
    );

    for (let number = 1; number <= 2; number += 1) {
      fireEvent.click(screen.getByRole("button", { name: "创建参与者" }));
      expect(screen.getByDisplayValue(`参与者 ${number}`)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "max" })).toHaveClass(
        "cc-dialog__option--selected",
      );
      fireEvent.click(screen.getByRole("button", { name: "保存参与者" }));
    }
    fireEvent.change(screen.getByPlaceholderText("需要多个模型独立判断什么问题？"), {
      target: { value: "Claude 强度能否冻结？" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建并开始第 1 轮" }));

    await waitFor(() => expect(onCreate).toHaveBeenCalledOnce());
    expect(onCreate.mock.calls[0][0].participants).toEqual([
      expect.objectContaining({ connection_id: "claude-glm-2", model: "opus", effort: "max", permission_mode: "dontAsk", permission_preset: null }),
      expect.objectContaining({ connection_id: "claude-glm-2", model: "opus", effort: "max", permission_mode: "dontAsk", permission_preset: null }),
    ]);
  });

  it("lets each participant choose and submit a runtime-specific permission", async () => {
    const onCreate = vi.fn();
    render(
      <NewDiscussionDialog
        connections={connections}
        sessionConfigurations={configurations}
        loading={false}
        creating={false}
        initialWorkdir="/repo"
        onChooseWorkdir={() => {}}
        onCreate={onCreate}
        onCancel={() => {}}
      />,
    );

    for (let number = 1; number <= 2; number += 1) {
      fireEvent.click(screen.getByRole("button", { name: "创建参与者" }));
      fireEvent.click(screen.getByRole("button", { name: "workspace-write" }));
      expect(screen.getByRole("status")).toHaveTextContent(
        "需要额外人工审批的操作会在本轮拒绝",
      );
      expect(screen.queryByText(/本轮会暂停并等待确认/)).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: "保存参与者" }));
    }
    expect(screen.getAllByText("workspace-write")).toHaveLength(2);

    fireEvent.change(screen.getByPlaceholderText("需要多个模型独立判断什么问题？"), {
      target: { value: "参与者能否执行工作区工具？" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建并开始第 1 轮" }));

    await waitFor(() => expect(onCreate).toHaveBeenCalledOnce());
    expect(onCreate.mock.calls[0][0].participants).toEqual([
      expect.objectContaining({ permission_mode: null, permission_preset: "workspace-write" }),
      expect.objectContaining({ permission_mode: null, permission_preset: "workspace-write" }),
    ]);
  });

  it("uses the shared bottom-anchored selector for automatic round limits", async () => {
    render(
      <NewDiscussionDialog
        connections={connections}
        sessionConfigurations={configurations}
        loading={false}
        creating={false}
        initialWorkdir="/repo"
        onChooseWorkdir={() => {}}
        onCreate={() => {}}
        onCancel={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole("radio", { name: /自动推进/ }));
    const trigger = screen.getByRole("combobox", { name: "最多轮数" });
    expect(trigger).toHaveTextContent("最多 3 轮");
    fireEvent.click(trigger);

    const listbox = await screen.findByRole("listbox");
    expect(listbox.closest("[data-side]")).toHaveAttribute("data-side", "bottom");
    expect(listbox).not.toContainElement(trigger);
  });
});
