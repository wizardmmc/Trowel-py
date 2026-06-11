import { describe, it, expect, vi, beforeEach } from "vitest";
import { useReviewStore } from "../stores/reviewStore";
import * as client from "../api/client";

// Mock the API client module
vi.mock("../api/client", () => ({
  getDueCards: vi.fn(),
  submitReview: vi.fn(),
  getSessionStats: vi.fn(),
}));

const mockGetDueCards = vi.mocked(client.getDueCards);
const mockSubmitReview = vi.mocked(client.submitReview);
const mockGetSessionStats = vi.mocked(client.getSessionStats);

beforeEach(() => {
  vi.clearAllMocks();
  // Reset zustand store state between tests
  useReviewStore.setState({
    dueCards: [],
    currentIndex: 0,
    loading: false,
    error: null,
    sessionComplete: false,
    sessionStats: null,
    sessionStartTime: null,
  });
});

function makeDueCard(id: string): client.DueCard {
  return {
    card: {
      id,
      title: `Card ${id}`,
      category: "test",
      explanation: "An explanation that is long enough for validation.",
      example: null,
      difficulty: 3,
      source: null,
      tags: [],
      status: "active",
      created_at: "2026-01-01T00:00:00",
      updated_at: "2026-01-01T00:00:00",
    },
    fsrs_state: {
      card_id: id,
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
  };
}

describe("reviewStore", () => {
  describe("loadDueCards", () => {
    it("loads due cards from the API", async () => {
      const cards = [makeDueCard("a"), makeDueCard("b")];
      mockGetDueCards.mockResolvedValueOnce(cards);

      await useReviewStore.getState().loadDueCards();

      const state = useReviewStore.getState();
      expect(state.dueCards).toHaveLength(2);
      expect(state.loading).toBe(false);
      expect(state.error).toBeNull();
      expect(state.sessionComplete).toBe(false);
    });

    it("sets sessionComplete when no cards are due", async () => {
      mockGetDueCards.mockResolvedValueOnce([]);

      await useReviewStore.getState().loadDueCards();

      const state = useReviewStore.getState();
      expect(state.dueCards).toHaveLength(0);
      expect(state.sessionComplete).toBe(true);
    });

    it("stores error on API failure", async () => {
      mockGetDueCards.mockRejectedValueOnce(new Error("Network error"));

      await useReviewStore.getState().loadDueCards();

      const state = useReviewStore.getState();
      expect(state.error).toBe("Network error");
      expect(state.loading).toBe(false);
    });
  });

  describe("rateCard", () => {
    it("submits rating and advances to next card", async () => {
      const cards = [makeDueCard("a"), makeDueCard("b")];
      mockGetDueCards.mockResolvedValueOnce(cards);
      await useReviewStore.getState().loadDueCards();

      mockSubmitReview.mockResolvedValueOnce({
        card: cards[0].card,
        fsrs_state: cards[0].fsrs_state,
        review_log: {
          id: "log1",
          card_id: "a",
          rating: 3,
          state: 1,
          elapsed_days: 0,
          scheduled_days: 1,
          duration_ms: null,
          created_at: "2026-01-01T00:00:00",
        },
        plant_stage: "sprout",
        plant_changed: true,
      });

      await useReviewStore.getState().rateCard(3);

      const state = useReviewStore.getState();
      expect(state.dueCards).toHaveLength(1);
      expect(state.dueCards[0].card.id).toBe("b");
      expect(state.currentIndex).toBe(0);
    });

    it("completes session when last card is rated", async () => {
      const cards = [makeDueCard("a")];
      mockGetDueCards.mockResolvedValueOnce(cards);
      await useReviewStore.getState().loadDueCards();

      mockSubmitReview.mockResolvedValueOnce({
        card: cards[0].card,
        fsrs_state: cards[0].fsrs_state,
        review_log: {
          id: "log1",
          card_id: "a",
          rating: 3,
          state: 1,
          elapsed_days: 0,
          scheduled_days: 1,
          duration_ms: null,
          created_at: "2026-01-01T00:00:00",
        },
        plant_stage: "sprout",
        plant_changed: true,
      });
      mockGetSessionStats.mockResolvedValueOnce({
        total: 1,
        avg_rating: 3.0,
        accuracy: 100.0,
      });

      await useReviewStore.getState().rateCard(3);

      const state = useReviewStore.getState();
      expect(state.sessionComplete).toBe(true);
      expect(state.sessionStats).toEqual({
        total: 1,
        avg_rating: 3.0,
        accuracy: 100.0,
      });
    });
  });

  describe("resetSession", () => {
    it("clears all session state", async () => {
      const cards = [makeDueCard("a")];
      mockGetDueCards.mockResolvedValueOnce(cards);
      await useReviewStore.getState().loadDueCards();

      useReviewStore.getState().resetSession();

      const state = useReviewStore.getState();
      expect(state.dueCards).toHaveLength(0);
      expect(state.currentIndex).toBe(0);
      expect(state.sessionComplete).toBe(false);
      expect(state.sessionStats).toBeNull();
    });
  });
});
