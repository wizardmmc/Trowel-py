import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { NewSessionDialog } from "../components/cc/NewSessionDialog";
import { CODEX_MODELS, createButton } from "./newSessionDialogFixtures";

describe("NewSessionDialog settings", () => {
  it("defaults to Claude + both switches ON + visible bypass permission", () => {
    render(
      <NewSessionDialog
        workdir="/wd"
        onCreate={() => {}}
        onCancel={() => {}}
      />,
    );
    const radios = screen.getAllByRole("radio");
    expect(radios[0]).toHaveAttribute("aria-checked", "true");
    expect(radios[1]).toHaveAttribute("aria-checked", "false");
    const switches = screen.getAllByRole("switch");
    expect(switches[0]).toHaveAttribute("aria-checked", "true");
    expect(switches[1]).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("button", { name: /跟随 CC/ })).toHaveClass(
      "cc-dialog__option--selected",
    );
  });

  it("create fires onCreate with runtime + M/P + model/effort/permission", () => {
    const onCreate = vi.fn();
    render(
      <NewSessionDialog
        workdir="/wd"
        onCreate={onCreate}
        onCancel={() => {}}
        codexModels={CODEX_MODELS}
      />,
    );
    fireEvent.click(screen.getAllByRole("radio")[1]);
    fireEvent.click(screen.getByText("gpt-5.6-sol"));
    fireEvent.click(screen.getByText("ultra"));
    fireEvent.click(screen.getByText("workspace-write"));
    fireEvent.click(screen.getAllByRole("switch")[0]);
    fireEvent.click(createButton());
    expect(onCreate).toHaveBeenCalledWith({
      runtime: "codex",
      memory_enabled: false,
      profile_enabled: true,
      model: "gpt-5.6-sol",
      effort: "ultra",
      permission_mode: "",
      permission_preset: "workspace-write",
    });
  });

  it("Codex model switch shows only native efforts and falls back to default", () => {
    const onCreate = vi.fn();
    render(
      <NewSessionDialog
        workdir="/wd"
        onCreate={onCreate}
        onCancel={() => {}}
        codexModels={CODEX_MODELS}
      />,
    );
    fireEvent.click(screen.getAllByRole("radio")[1]);
    fireEvent.click(screen.getByRole("button", { name: "ultra" }));
    fireEvent.click(screen.getByRole("button", { name: "gpt-5.6-luna" }));
    expect(
      screen.queryByRole("button", { name: "ultra" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "medium" })).toHaveClass(
      "cc-dialog__option--selected",
    );
    fireEvent.click(createButton());
    expect(onCreate.mock.calls[0][0]).toMatchObject({
      model: "gpt-5.6-luna",
      effort: "medium",
    });
  });

  it("Codex catalog failure is visible and never shows a static Sol fallback", () => {
    render(
      <NewSessionDialog
        workdir="/wd"
        onCreate={() => {}}
        onCancel={() => {}}
        codexModels={[]}
        codexCatalogError="catalog offline"
      />,
    );
    fireEvent.click(screen.getAllByRole("radio")[1]);
    expect(screen.getByRole("alert")).toHaveTextContent("catalog offline");
    expect(screen.queryByText("gpt-5.6-sol")).not.toBeInTheDocument();
    expect(createButton()).toBeDisabled();
  });

  it("adopts native defaults when the Codex catalog arrives after opening", () => {
    const onCreate = vi.fn();
    const { rerender } = render(
      <NewSessionDialog
        workdir="/wd"
        onCreate={onCreate}
        onCancel={() => {}}
        codexModels={[]}
      />,
    );
    fireEvent.click(screen.getAllByRole("radio")[1]);
    expect(createButton()).toBeDisabled();

    rerender(
      <NewSessionDialog
        workdir="/wd"
        onCreate={onCreate}
        onCancel={() => {}}
        codexModels={CODEX_MODELS}
      />,
    );
    expect(screen.getByRole("button", { name: "gpt-5.6-sol" })).toHaveClass(
      "cc-dialog__option--selected",
    );
    expect(screen.getByRole("button", { name: "low" })).toHaveClass(
      "cc-dialog__option--selected",
    );
    fireEvent.click(createButton());
    expect(onCreate.mock.calls[0][0]).toMatchObject({
      model: "gpt-5.6-sol",
      effort: "low",
      permission_preset: "follow",
    });
  });

  it("requires one explicit danger confirmation before Full access is selected", () => {
    const onCreate = vi.fn();
    render(
      <NewSessionDialog
        workdir="/wd"
        onCreate={onCreate}
        onCancel={() => {}}
        codexModels={CODEX_MODELS}
      />,
    );
    fireEvent.click(screen.getAllByRole("radio")[1]);
    fireEvent.click(screen.getByRole("button", { name: "Full access" }));
    expect(screen.getByRole("alert")).toHaveTextContent("关闭 sandbox");
    fireEvent.click(screen.getByRole("button", { name: "确认 Full access" }));
    fireEvent.click(createButton());
    expect(onCreate.mock.calls[0][0].permission_preset).toBe(
      "danger-full-access",
    );
  });

  it("restores the last Codex config but requires Full access confirmation again", () => {
    const onCreate = vi.fn();
    render(
      <NewSessionDialog
        workdir="/wd"
        initialConfig={{
          runtime: "codex",
          model: "gpt-5.6-sol",
          effort: "ultra",
          permission_mode: "",
          permission_preset: "danger-full-access",
          memory_enabled: false,
          profile_enabled: true,
        }}
        codexModels={CODEX_MODELS}
        onCreate={onCreate}
        onCancel={() => {}}
      />,
    );

    expect(screen.getByRole("button", { name: "Full access" })).toHaveClass(
      "cc-dialog__option--selected",
    );
    expect(screen.getByRole("alert")).toHaveTextContent("关闭 sandbox");
    expect(createButton()).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "确认 Full access" }));
    fireEvent.click(createButton());
    expect(onCreate.mock.calls[0][0]).toMatchObject({
      runtime: "codex",
      model: "gpt-5.6-sol",
      effort: "ultra",
      permission_preset: "danger-full-access",
      memory_enabled: false,
    });
  });

  it("falls back to native Codex defaults when remembered catalog values disappeared", () => {
    const onCreate = vi.fn();
    render(
      <NewSessionDialog
        workdir="/wd"
        initialConfig={{
          runtime: "codex",
          model: "removed-model",
          effort: "removed-effort",
          permission_mode: "",
          permission_preset: "workspace-write",
          memory_enabled: true,
          profile_enabled: true,
        }}
        codexModels={CODEX_MODELS}
        onCreate={onCreate}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "gpt-5.6-sol" })).toHaveClass(
      "cc-dialog__option--selected",
    );
    expect(screen.getByRole("button", { name: "low" })).toHaveClass(
      "cc-dialog__option--selected",
    );
    fireEvent.click(createButton());
    expect(onCreate.mock.calls[0][0]).toMatchObject({
      model: "gpt-5.6-sol",
      effort: "low",
    });
  });

  it("warns that workspace approvals pause for confirmation", () => {
    render(
      <NewSessionDialog
        workdir="/wd"
        onCreate={() => {}}
        onCancel={() => {}}
        codexModels={CODEX_MODELS}
      />,
    );
    fireEvent.click(screen.getAllByRole("radio")[1]);
    fireEvent.click(screen.getByRole("button", { name: "workspace-write" }));
    expect(screen.getByRole("status")).toHaveTextContent("原生审批请求");
  });

  it("switching runtime resets model/effort/permission (no leak across runtimes)", () => {
    const onCreate = vi.fn();
    render(
      <NewSessionDialog
        workdir="/wd"
        onCreate={onCreate}
        onCancel={() => {}}
        codexModels={CODEX_MODELS}
      />,
    );
    fireEvent.click(screen.getAllByRole("radio")[1]);
    fireEvent.click(screen.getByText("gpt-5.6-sol"));
    fireEvent.click(screen.getAllByRole("radio")[0]);
    fireEvent.click(createButton());
    const config = onCreate.mock.calls[0][0];
    expect(config.model).toBe("");
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
});
