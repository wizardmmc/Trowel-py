import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  getDueCards,
  submitReview,
  getSessionStats,
  type DueCard,
} from "../api/client";

// Mock global fetch
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

function mockResponse<T>(data: T, success = true) {
  return {
    ok: true,
    json: () =>
      Promise.resolve({
        success,
        data,
        error: null,
      }),
  };
}

beforeEach(() => {
  mockFetch.mockReset();
});

describe("getDueCards", () => {
  it("fetches due cards from the review API", async () => {
    const mockDueCards: DueCard[] = [
      {
        card: {
          id: "card1",
          title: "Test Card",
          category: "test",
          explanation: "An explanation text that is long enough",
          example: null,
          difficulty: 3,
          source: null,
          tags: [],
          status: "active",
          created_at: "2026-01-01T00:00:00",
          updated_at: "2026-01-01T00:00:00",
        },
        fsrs_state: {
          card_id: "card1",
          stability: 0.0,
          difficulty: 0.0,
          elapsed_days: 0,
          scheduled_days: 0,
          reps: 0,
          lapses: 0,
          state: 0,
          due: "2026-01-01T00:00:00",
          last_review: null,
        },
        plant_stage: "seed",
      },
    ];

    mockFetch.mockResolvedValueOnce(mockResponse(mockDueCards));

    const result = await getDueCards();
    expect(result).toHaveLength(1);
    expect(result[0].card.id).toBe("card1");
    expect(result[0].plant_stage).toBe("seed");
    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/review/due",
      undefined,
    );
  });

  it("returns empty array when no cards are due", async () => {
    mockFetch.mockResolvedValueOnce(mockResponse([]));
    const result = await getDueCards();
    expect(result).toHaveLength(0);
  });
});

describe("submitReview", () => {
  it("submits a rating for a card", async () => {
    const mockResponse_data = {
      card: { id: "card1", title: "Test" },
      fsrs_state: { card_id: "card1", state: 1 },
      review_log: { id: "log1", card_id: "card1", rating: 3 },
      plant_stage: "sprout",
      plant_changed: true,
    };

    mockFetch.mockResolvedValueOnce(mockResponse(mockResponse_data));

    const result = await submitReview("card1", 3);
    expect(result.plant_stage).toBe("sprout");
    expect(result.plant_changed).toBe(true);
    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/review/submit",
      expect.objectContaining({ method: "POST" }),
    );
  });
});

describe("getSessionStats", () => {
  it("fetches session stats since a given timestamp", async () => {
    const mockStats = { total: 5, avg_rating: 3.2, accuracy: 80.0 };

    mockFetch.mockResolvedValueOnce(mockResponse(mockStats));

    const result = await getSessionStats("2026-01-01T00:00:00");
    expect(result.total).toBe(5);
    expect(result.accuracy).toBe(80.0);
    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/review/session-stats?since=2026-01-01T00%3A00%3A00",
      undefined,
    );
  });
});
