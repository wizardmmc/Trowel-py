import { describe, it, expect, vi, beforeEach } from "vitest";
import { useEventStore } from "../stores/eventStore";
import type { EventLog } from "../api/client";

vi.mock("../api/client", () => ({
  triggerEvent: vi.fn(),
}));

import { triggerEvent } from "../api/client";

const testEvent: EventLog = {
  id: "e1",
  player_id: "default",
  event_type: "gift",
  reward_xp: 15,
  reward_coin: 5,
  reward_item_id: null,
  description: "收到一份小礼物",
  card_id: null,
  triggered_at: "2026-06-23T10:00:00",
};

describe("eventStore", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useEventStore.setState({ currentEvent: null, checking: false, error: null });
  });

  it("starts with no current event and not checking", () => {
    const state = useEventStore.getState();
    expect(state.currentEvent).toBeNull();
    expect(state.checking).toBe(false);
    expect(state.error).toBeNull();
  });

  it("sets currentEvent when trigger returns an event", async () => {
    vi.mocked(triggerEvent).mockResolvedValue(testEvent);
    await useEventStore.getState().checkForEvent();
    expect(useEventStore.getState().currentEvent).toEqual(testEvent);
    expect(useEventStore.getState().error).toBeNull();
  });

  it("leaves currentEvent null when nothing fires (cooldown / no eligible)", async () => {
    vi.mocked(triggerEvent).mockResolvedValue(null);
    await useEventStore.getState().checkForEvent();
    expect(useEventStore.getState().currentEvent).toBeNull();
  });

  it("sets error and keeps currentEvent null when trigger throws", async () => {
    vi.mocked(triggerEvent).mockRejectedValue(new Error("network down"));
    await useEventStore.getState().checkForEvent();
    expect(useEventStore.getState().currentEvent).toBeNull();
    expect(useEventStore.getState().error).toBe("network down");
  });

  it("claimReward clears currentEvent", async () => {
    vi.mocked(triggerEvent).mockResolvedValue(testEvent);
    await useEventStore.getState().checkForEvent();
    useEventStore.getState().claimReward();
    expect(useEventStore.getState().currentEvent).toBeNull();
  });
});
