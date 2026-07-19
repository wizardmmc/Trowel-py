import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PermissionFactsChip } from "../components/cc/PermissionFactsChip";

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
    fireEvent.click(screen.getByRole("button", { name: /permission:/ }));
    expect(screen.getByRole("dialog")).toHaveTextContent("follow");
    expect(screen.getByRole("dialog")).toHaveTextContent("read-only");
    expect(screen.getByRole("dialog")).toHaveTextContent("on-request");
    expect(screen.getByRole("dialog")).toHaveTextContent("disabled");
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
    const chip = screen.getByRole("button", { name: /Full access · never/ });
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
