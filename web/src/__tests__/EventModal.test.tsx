import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { EventModal } from "../components/events/EventModal";
import { EVENT_ICONS } from "../components/pet/itemCatalog";
import type { EventLog } from "../api/client";

function makeEvent(overrides: Partial<EventLog>): EventLog {
  return {
    id: "e1",
    player_id: "default",
    event_type: "gift",
    reward_xp: 15,
    reward_coin: 5,
    reward_item_id: null,
    description: "收到一份小礼物",
    card_id: null,
    triggered_at: "2026-06-23T10:00:00",
    ...overrides,
  };
}

describe("EventModal", () => {
  it("renders nothing when event is null", () => {
    render(<EventModal event={null} onClaim={vi.fn()} />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("renders the description and claim button when an event is present", () => {
    render(<EventModal event={makeEvent({})} onClaim={vi.fn()} />);
    expect(screen.getByText("收到一份小礼物")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /领取奖励/ }),
    ).toBeInTheDocument();
  });

  it("shows the event-type icon", () => {
    render(<EventModal event={makeEvent({ event_type: "gift" })} onClaim={vi.fn()} />);
    expect(screen.getByText(EVENT_ICONS.gift)).toBeInTheDocument();
  });

  it("renders xp and coin rewards when positive", () => {
    render(
      <EventModal
        event={makeEvent({ reward_xp: 20, reward_coin: 10 })}
        onClaim={vi.fn()}
      />,
    );
    expect(screen.getByText("+20")).toBeInTheDocument();
    expect(screen.getByText("+10")).toBeInTheDocument();
  });

  it("renders the item reward label when reward_item_id is set", () => {
    render(
      <EventModal
        event={makeEvent({ reward_item_id: "food_basic" })}
        onClaim={vi.fn()}
      />,
    );
    expect(screen.getByText(/基础食物/)).toBeInTheDocument();
  });

  it("calls onClaim when the claim button is clicked", () => {
    const onClaim = vi.fn();
    render(<EventModal event={makeEvent({})} onClaim={onClaim} />);
    fireEvent.click(screen.getByRole("button", { name: /领取奖励/ }));
    expect(onClaim).toHaveBeenCalledTimes(1);
  });

  it("calls onClaim when the overlay backdrop is clicked", () => {
    const onClaim = vi.fn();
    render(<EventModal event={makeEvent({})} onClaim={onClaim} />);
    fireEvent.click(screen.getByTestId("event-modal-overlay"));
    expect(onClaim).toHaveBeenCalledTimes(1);
  });
});
