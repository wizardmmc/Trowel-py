import { create } from "zustand";
import {
  getGardenPlants,
  getGardenStats,
  searchCards,
  type GardenPlant,
  type GardenStatsData,
} from "../api/client";

export type SortMode = "category" | "time";

export interface GardenState {
  readonly plants: readonly GardenPlant[];
  readonly loading: boolean;
  readonly error: string | null;
  readonly searchQuery: string;
  readonly sortBy: SortMode;
  readonly expandedCategories: ReadonlySet<string>;
  readonly selectedPlantId: string | null;
  readonly stats: GardenStatsData | null;

  fetchGarden: () => Promise<void>;
  searchPlants: (q: string) => Promise<void>;
  clearSearch: () => void;
  setSortBy: (sort: SortMode) => void;
  toggleCategory: (cat: string) => void;
  selectPlant: (id: string | null) => void;
  refreshStats: () => Promise<void>;
}

export const useGardenStore = create<GardenState>((set, get) => ({
  plants: [],
  loading: false,
  error: null,
  searchQuery: "",
  sortBy: "category",
  expandedCategories: new Set<string>(),
  selectedPlantId: null,
  stats: null,

  fetchGarden: async () => {
    set({ loading: true, error: null });
    try {
      const plants = await getGardenPlants();
      set({ plants, loading: false });
      void get().refreshStats();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load garden";
      set({ loading: false, error: message });
    }
  },

  searchPlants: async (q: string) => {
    const trimmed = q.trim().slice(0, 100);
    set({ searchQuery: q });
    if (trimmed.length === 0) {
      get().clearSearch();
      return;
    }
    set({ loading: true, error: null });
    try {
      const cards = await searchCards(trimmed) as unknown as Array<{
        id: string;
        title: string;
        category: string;
        explanation: string;
      }>;
      const plants: GardenPlant[] = cards.map((c) => ({
        card_id: c.id,
        title: c.title,
        category: c.category,
        explanation: c.explanation,
        plant_stage: "seed" as const,
        fsrs_state: null,
        due: null,
        reps: 0,
      }));
      set({ plants, loading: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Search failed";
      set({ loading: false, error: message });
    }
  },

  clearSearch: () => {
    set({ searchQuery: "" });
    get().fetchGarden();
  },

  setSortBy: (sort: SortMode) => {
    set({ sortBy: sort });
  },

  toggleCategory: (cat: string) => {
    const current = get().expandedCategories;
    const next = current.has(cat)
      ? new Set([...current].filter((c) => c !== cat))
      : new Set([...current, cat]);
    set({ expandedCategories: next });
  },

  selectPlant: (id: string | null) => {
    set({ selectedPlantId: id });
  },

  refreshStats: async () => {
    try {
      const stats = await getGardenStats();
      set({ stats });
    } catch {
    }
  },
}));
