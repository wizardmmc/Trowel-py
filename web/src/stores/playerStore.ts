import { create } from "zustand";
import {
  fetchPlayer as fetchPlayerApi,
  fetchInventory as fetchInventoryApi,
  buyItem as buyItemApi,
  type PlayerProfile,
  type InventoryItem,
} from "../api/client";

export interface PlayerState {
  readonly player: PlayerProfile | null;
  readonly inventory: readonly InventoryItem[];
  readonly loading: boolean;
  readonly error: string | null;

  fetchProfile: () => Promise<void>;
  fetchInventory: () => Promise<void>;
  buyItem: (itemId: string) => Promise<void>;
}

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
