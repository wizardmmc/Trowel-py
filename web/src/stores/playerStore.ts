import { create } from "zustand";
import {
  fetchPlayer as fetchPlayerApi,
  fetchInventory as fetchInventoryApi,
  buyItem as buyItemApi,
  type PlayerProfile,
  type InventoryItem,
} from "../api/client";

export interface PlayerState {
  /** the default player's profile, or null before first load */
  readonly player: PlayerProfile | null;
  /** every owned item (food + hats); row id is item.id, catalog id is item.item_id */
  readonly inventory: readonly InventoryItem[];
  readonly loading: boolean;
  readonly error: string | null;

  fetchProfile: () => Promise<void>;
  fetchInventory: () => Promise<void>;
  /** buy an item by catalog id; rethrows on failure so callers can branch */
  buyItem: (itemId: string) => Promise<void>;
}

// API functions are imported with an `Api` suffix so the store actions
// (fetchPlayer, buyItem, ...) don't shadow them inside the action bodies.
export const usePlayerStore = create<PlayerState>((set, get) => ({
  player: null,
  inventory: [],
  loading: false,
  error: null,

  fetchProfile: async () => {
    set({ loading: true, error: null });
    try {
      const player = await fetchPlayerApi();
      set({ player, loading: false });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to load player";
      set({ loading: false, error: message });
    }
  },

  fetchInventory: async () => {
    try {
      const inventory = await fetchInventoryApi();
      set({ inventory });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to load inventory";
      set({ error: message });
    }
  },

  buyItem: async (itemId: string) => {
    set({ loading: true, error: null });
    try {
      // buy only returns the catalog id + type, not the new row id. re-fetch
      // both the profile (coins were spent server-side) and the inventory
      // (the granted row must be resolved by row id for feed/equip).
      await buyItemApi(itemId);
      await get().fetchProfile();
      await get().fetchInventory();
      set({ loading: false });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to buy item";
      set({ loading: false, error: message });
      throw err;
    }
  },
}));
