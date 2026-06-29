import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NotificationBanner } from "../components/cards/NotificationBanner";

describe("NotificationBanner", () => {
  it("renders nothing when count is 0", () => {
    const { container } = render(
      <NotificationBanner count={0} onClick={vi.fn()} />
    );

    expect(container.innerHTML).toBe("");
  });

  it("shows singular message for 1 card", () => {
    render(<NotificationBanner count={1} onClick={vi.fn()} />);

    expect(screen.getByText("有 1 张卡片待审核")).toBeInTheDocument();
  });

  it("shows plural message for multiple cards", () => {
    render(<NotificationBanner count={3} onClick={vi.fn()} />);

    expect(screen.getByText("有 3 张卡片待审核")).toBeInTheDocument();
  });

  it("calls onClick when clicked", async () => {
    const onClick = vi.fn();
    render(<NotificationBanner count={2} onClick={onClick} />);

    await userEvent.click(screen.getByTestId("notification-banner"));

    expect(onClick).toHaveBeenCalledOnce();
  });
});
