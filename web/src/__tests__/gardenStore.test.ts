import { describe, it, expect, vi, beforeEach } from "vitest";
import { useGardenStore } from "../stores/gardenStore";
import type { GardenPlant, GardenStatsData } from "../api/client";

vi.mock("../api/client", () => ({
  getGardenPlants: vi.fn(),
  getGardenStats: vi.fn(),
  searchCards: vi.fn(),
}));

import { getGardenPlants, getGardenStats, searchCards } from "../api/client";

const mockPlants: GardenPlant[] = [
  {
    card_id: "c1",
    title: "Python Decorators",
    category: "python",
    explanation: "Decorators wrap functions",
    plant_stage: "tree",
    fsrs_state: 2,
    due: "2099-01-01T00:00:00Z",
    reps: 10,
  },
  {
    card_id: "c2",
    title: "React Hooks",
    category: "react",
    explanation: "Hooks manage state",
    plant_stage: "seed",
    fsrs_state: null,
    due: null,
    reps: 0,
  },
];

const mockStats: GardenStatsData = {
  total_plants: 2,
  due_count: 1,
  flowering_rate: 50.0,
};

describe("gardenStore", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useGardenStore.setState({
      plants: [],
      loading: false,
      error: null,
      searchQuery: "",
      sortBy: "category",
      expandedCategories: new Set(),
      selectedPlantId: null,
      stats: null,
    });
  });

  it("fetchGarden loads plants and stats", async () => {
    vi.mocked(getGardenPlants).mockResolvedValue(mockPlants);
    vi.mocked(getGardenStats).mockResolvedValue(mockStats);

    await useGardenStore.getState().fetchGarden();

    const state = useGardenStore.getState();
    expect(state.plants).toHaveLength(2);
    expect(state.plants[0].card_id).toBe("c1");
    expect(state.loading).toBe(false);
    expect(state.error).toBeNull();

    await vi.waitFor(() => {
      expect(useGardenStore.getState().stats).toEqual(mockStats);
    });
  });

  it("fetchGarden handles error", async () => {
    vi.mocked(getGardenPlants).mockRejectedValue(new Error("Network error"));

    await useGardenStore.getState().fetchGarden();

    const state = useGardenStore.getState();
    expect(state.error).toBe("Network error");
    expect(state.plants).toHaveLength(0);
  });

  it("searchPlants calls searchCards", async () => {
    vi.mocked(searchCards).mockResolvedValue([]);

    await useGardenStore.getState().searchPlants("decorators");

    expect(searchCards).toHaveBeenCalledWith("decorators");
    expect(useGardenStore.getState().searchQuery).toBe("decorators");
  });

  it("searchPlants with empty query clears search", async () => {
    vi.mocked(getGardenPlants).mockResolvedValue(mockPlants);
    useGardenStore.setState({ searchQuery: "old query" });

    await useGardenStore.getState().searchPlants("   ");

    expect(searchCards).not.toHaveBeenCalled();
    expect(useGardenStore.getState().searchQuery).toBe("");
  });

  it("toggleCategory adds and removes", () => {
    useGardenStore.getState().toggleCategory("python");
    expect(useGardenStore.getState().expandedCategories.has("python")).toBe(true);

    useGardenStore.getState().toggleCategory("python");
    expect(useGardenStore.getState().expandedCategories.has("python")).toBe(false);
  });

  it("selectPlant sets selectedPlantId", () => {
    useGardenStore.getState().selectPlant("c1");
    expect(useGardenStore.getState().selectedPlantId).toBe("c1");

    useGardenStore.getState().selectPlant(null);
    expect(useGardenStore.getState().selectedPlantId).toBeNull();
  });

  it("setSortBy changes sort mode", () => {
    useGardenStore.getState().setSortBy("time");
    expect(useGardenStore.getState().sortBy).toBe("time");
  });

  it("refreshStats updates stats", async () => {
    vi.mocked(getGardenStats).mockResolvedValue(mockStats);

    await useGardenStore.getState().refreshStats();

    expect(useGardenStore.getState().stats).toEqual(mockStats);
  });

  it("refreshStats silently ignores errors", async () => {
    vi.mocked(getGardenStats).mockRejectedValue(new Error("fail"));

    await useGardenStore.getState().refreshStats();

    expect(useGardenStore.getState().stats).toBeNull();
  });
});
