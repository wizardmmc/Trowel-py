import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  ACTIVE_SESSION_PRESETS,
  PermissionFactsChip,
} from "../components/cc/PermissionFactsChip";

describe("PermissionFactsChip", () => {
  it("keeps requested follow separate from native effective facts", () => {
    render(
      <PermissionFactsChip
        requested="follow"
        profile=":read-only"
        sandbox="read-only"
        approval="on-request"
        network={false}
        label="Read only · on-request"
      />,
    );
    const chip = screen.getByRole("button", { name: /permission:/ });
    // requested 反映在 chip 主显示，不混进 effective facts 区。
    expect(chip).toHaveTextContent("Follow");
    fireEvent.click(chip);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("read-only");
    expect(dialog).toHaveTextContent("on-request");
    expect(dialog).toHaveTextContent("disabled");
  });

  it("reflects requested workspace-write on chip even when effective label stays Full access", () => {
    // PATCH 改 requested 后 native effective 尚未刷新：chip 主显示必须反映
    // 新请求，否则用户看不出权限已切换。
    render(
      <PermissionFactsChip
        requested="workspace-write"
        profile=":danger-full-access"
        sandbox="danger-full-access"
        approval="never"
        network
        label="Full access · never"
      />,
    );
    const chip = screen.getByRole("button", { name: /permission:/ });
    expect(chip).toHaveTextContent("Workspace write");
    expect(chip).not.toHaveTextContent("Full access · never");
  });

  it("keeps the requested row out of the native effective facts section", () => {
    render(
      <PermissionFactsChip
        requested="workspace-write"
        profile=":danger-full-access"
        sandbox="danger-full-access"
        approval="never"
        network
        label="Full access · never"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /permission:/ }));
    // requested 是本地请求、不是 native 事实，不能出现在 effective 区。
    const effectiveSection = screen.getByRole("group", {
      name: "原生 effective facts",
    });
    expect(within(effectiveSection).queryByText("requested")).toBeNull();
    expect(effectiveSection).toHaveTextContent("danger-full-access");
    expect(effectiveSection).toHaveTextContent("never");
  });

  it("uses danger semantics only for the full effective tuple", () => {
    render(
      <PermissionFactsChip
        requested="danger-full-access"
        profile=":danger-full-access"
        sandbox="danger-full-access"
        approval="never"
        network
        label="Full access · never"
      />,
    );
    // requested 与 effective 都指向 Full access 时，chip 带 danger 样式。
    const chip = screen.getByRole("button", { name: /permission:/ });
    expect(chip).toHaveClass("cc-chip__btn--danger");
    fireEvent.click(chip);
    expect(screen.getByRole("alert")).toHaveTextContent("native 已确认无 sandbox");
  });

  it("does not present a requested preset as an effective native fact", () => {
    render(
      <PermissionFactsChip
        requested="danger-full-access"
        profile={null}
        sandbox={null}
        approval={null}
        network={null}
        label={null}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /待 native 确认/ }));
    expect(screen.getByRole("alert")).toHaveTextContent("已请求 Full access");
    expect(screen.getByRole("alert")).not.toHaveTextContent("native 已确认");
  });
});

describe("PermissionFactsChip preset selector", () => {
  it("renders selectable presets and reports non-danger choice immediately", () => {
    const onSelectPreset = vi.fn();
    render(
      <PermissionFactsChip
        requested="workspace-write"
        profile=":workspace-write"
        sandbox="workspace-write"
        approval="on-request"
        network={false}
        label="Workspace write · on-request"
        selectablePresets={["follow", "read-only", "workspace-write", "danger-full-access"]}
        selectedPreset="workspace-write"
        onSelectPreset={onSelectPreset}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /permission:/ }));

    const readOption = screen.getByRole("button", { name: /Read only/ });
    fireEvent.click(readOption);
    expect(onSelectPreset).toHaveBeenCalledWith("read-only");
  });

  it("requires a second confirmation before reporting Full access", () => {
    const onSelectPreset = vi.fn();
    render(
      <PermissionFactsChip
        requested="workspace-write"
        profile=":workspace-write"
        sandbox="workspace-write"
        approval="on-request"
        network={false}
        label="Workspace write · on-request"
        selectablePresets={["follow", "read-only", "workspace-write", "danger-full-access"]}
        selectedPreset="workspace-write"
        onSelectPreset={onSelectPreset}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /permission:/ }));

    const fullOption = screen.getByRole("button", { name: /Full access/ });
    fireEvent.click(fullOption);
    // 选中 Full access 不立即下发，需二次确认。
    expect(onSelectPreset).not.toHaveBeenCalled();

    const confirm = screen.getByRole("button", { name: /确认.*Full access/ });
    fireEvent.click(confirm);
    expect(onSelectPreset).toHaveBeenCalledWith("danger-full-access");
  });

  it("omits the preset selector when onSelectPreset is absent", () => {
    render(
      <PermissionFactsChip
        requested="follow"
        profile={null}
        sandbox={null}
        approval={null}
        network={null}
        label={null}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /permission:/ }));
    expect(screen.queryByRole("button", { name: /Full access/ })).toBeNull();
  });

  it("active-session preset list omits Follow", () => {
    // 活动会话 Follow 在 sticky turn override 后没有确定的恢复语义；
    // SessionComposer 传入不含 follow 的列表。
    expect(ACTIVE_SESSION_PRESETS).not.toContain("follow");
    expect(ACTIVE_SESSION_PRESETS).toEqual([
      "read-only",
      "workspace-write",
      "danger-full-access",
    ]);

    const onSelectPreset = vi.fn();
    render(
      <PermissionFactsChip
        requested="workspace-write"
        profile=":workspace-write"
        sandbox="workspace-write"
        approval="on-request"
        network={false}
        label="Workspace write · on-request"
        selectablePresets={ACTIVE_SESSION_PRESETS}
        selectedPreset="workspace-write"
        onSelectPreset={onSelectPreset}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /permission:/ }));
    expect(screen.queryByRole("button", { name: /^Follow$/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Read only/ })).toBeInTheDocument();
  });
});
