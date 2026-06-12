import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  getGardenPlants,
  getGardenStats,
  searchCards,
} from "../api/client";

describe("Garden API client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("getGardenPlants fetches /api/garden/plants", async () => {
    const plants = [
      {
        card_id: "c1",
        title: "Test",
        category: "python",
        explanation: "test explanation that is long enough",
        plant_stage: "seed",
        fsrs_state: null,
        due: null,
        reps: 0,
      },
    ];

    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ success: true, data: plants, error: null })),
    );

    const result = await getGardenPlants();

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/garden/plants",
      undefined,
    );
    expect(result).toHaveLength(1);
    expect(result[0].card_id).toBe("c1");
  });

  it("getGardenStats fetches /api/garden/stats", async () => {
    const stats = { total_plants: 5, due_count: 2, flowering_rate: 40.0 };

    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ success: true, data: stats, error: null })),
    );

    const result = await getGardenStats();

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/garden/stats",
      undefined,
    );
    expect(result.total_plants).toBe(5);
    expect(result.due_count).toBe(2);
  });

  it("searchCards fetches /api/cards/search", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ success: true, data: [], error: null })),
    );

    await searchCards("decorators");

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/cards/search?q=decorators",
      undefined,
    );
  });

  it("searchCards encodes special characters", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ success: true, data: [], error: null })),
    );

    await searchCards("c++ templates");

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/cards/search?q=c%2B%2B%20templates",
      undefined,
    );
  });
});
