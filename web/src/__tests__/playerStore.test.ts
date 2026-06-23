import { describe, it, expect, vi, beforeEach } from "vitest";
import { usePlayerStore } from "../stores/playerStore";
import type { PlayerProfile, InventoryItem } from "../api/client";

// Mock the HTTP layer; the store under test orchestrates state, not network.
vi.mock("../api/client", () => ({
  fetchPlayer: vi.fn(),
  fetchInventory: vi.fn(),
  buyItem: vi.fn(),
}));

import { fetchPlayer, fetchInventory, buyItem } from "../api/client";

const testPlayer: PlayerProfile = {
  id: "default",
  xp: 100,
  coins: 200,
  streak_days: 3,
  last_active: "2026-06-22T00:00:00",
  created_at: "2026-06-01T00:00:00",
  level: 2,
  xp_to_next_level: 200,
};

const testInventory: InventoryItem[] = [
  {
    id: "row-1",
    player_id: "default",
    item_id: "food_basic",
    item_type: "food",
    equipped: 0,
    obtained_at: "2026-06-22T00:00:00",
  },
  {
    id: "row-2",
    player_id: "default",
    item_id: "hat_straw",
    item_type: "hat",
    equipped: 1,
    obtained_at: "2026-06-22T00:00:00",
  },
];

describe("playerStore", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    usePlayerStore.setState({
      player: null,
      inventory: [],
      loading: false,
      error: null,
    });
  });

  it("fetchProfile loads the player profile", async () => {
    vi.mocked(fetchPlayer).mockResolvedValue(testPlayer);

    await usePlayerStore.getState().fetchProfile();

    expect(usePlayerStore.getState().player).toEqual(testPlayer);
    expect(usePlayerStore.getState().error).toBeNull();
  });

  it("fetchProfile stores the error message on failure", async () => {
    vi.mocked(fetchPlayer).mockRejectedValue(new Error("boom"));

    await usePlayerStore.getState().fetchProfile();

    expect(usePlayerStore.getState().player).toBeNull();
    expect(usePlayerStore.getState().error).toBe("boom");
  });

  it("fetchInventory loads the inventory list", async () => {
    vi.mocked(fetchInventory).mockResolvedValue(testInventory);

    await usePlayerStore.getState().fetchInventory();

    expect(usePlayerStore.getState().inventory).toEqual(testInventory);
  });

  it("buyItem refreshes both profile (coins) and inventory on success", async () => {
    vi.mocked(buyItem).mockResolvedValue({ item_id: "food_basic", item_type: "food" });
    vi.mocked(fetchPlayer).mockResolvedValue({ ...testPlayer, coins: 190 });
    vi.mocked(fetchInventory).mockResolvedValue([
      ...testInventory,
      {
        id: "row-3",
        player_id: "default",
        item_id: "food_basic",
        item_type: "food",
        equipped: 0,
        obtained_at: "2026-06-22T00:00:00",
      },
    ]);

    await usePlayerStore.getState().buyItem("food_basic");

    expect(buyItem).toHaveBeenCalledWith("food_basic");
    // after buying, the store re-fetches profile + inventory so the UI sees
    // the spent coins and the newly granted row.
    expect(fetchPlayer).toHaveBeenCalled();
    expect(fetchInventory).toHaveBeenCalled();
    expect(usePlayerStore.getState().player?.coins).toBe(190);
    expect(usePlayerStore.getState().inventory).toHaveLength(3);
    expect(usePlayerStore.getState().loading).toBe(false);
  });

  it("buyItem rethrows on failure so the caller can branch", async () => {
    vi.mocked(buyItem).mockRejectedValue(new Error("not enough coins"));

    await expect(
      usePlayerStore.getState().buyItem("hat_scholar"),
    ).rejects.toThrow("not enough coins");

    expect(usePlayerStore.getState().error).toBe("not enough coins");
    expect(usePlayerStore.getState().loading).toBe(false);
  });
});
