import { describe, expect, it } from "vitest";
import {
  makeDueCard,
  mockGetDueCards,
  mockGetSessionStats,
  mockReviewSuccess,
} from "./reviewStoreTestHarness";
import { useReviewStore } from "../stores/reviewStore";

describe("reviewStore session", () => {
  describe("loadDueCards", () => {
    it("loads due cards from the API", async () => {
      mockGetDueCards.mockResolvedValueOnce([
        makeDueCard("a"),
        makeDueCard("b"),
      ]);

      await useReviewStore.getState().loadDueCards();

      expect(useReviewStore.getState().dueCards).toHaveLength(2);
      expect(useReviewStore.getState().loading).toBe(false);
      expect(useReviewStore.getState().error).toBeNull();
      expect(useReviewStore.getState().sessionComplete).toBe(false);
    });

    it("sets sessionComplete when no cards are due", async () => {
      mockGetDueCards.mockResolvedValueOnce([]);

      await useReviewStore.getState().loadDueCards();

      expect(useReviewStore.getState().dueCards).toHaveLength(0);
      expect(useReviewStore.getState().sessionComplete).toBe(true);
    });

    it("stores error on API failure", async () => {
      mockGetDueCards.mockRejectedValueOnce(new Error("Network error"));

      await useReviewStore.getState().loadDueCards();

      expect(useReviewStore.getState().error).toBe("Network error");
      expect(useReviewStore.getState().loading).toBe(false);
    });
  });

  describe("rateCard", () => {
    it("submits rating and advances to next card", async () => {
      const cards = [makeDueCard("a"), makeDueCard("b")];
      mockGetDueCards.mockResolvedValueOnce(cards);
      await useReviewStore.getState().loadDueCards();
      mockReviewSuccess(cards[0]);

      await useReviewStore.getState().rateCard(3);

      expect(useReviewStore.getState().dueCards).toHaveLength(1);
      expect(useReviewStore.getState().dueCards[0].card.id).toBe("b");
      expect(useReviewStore.getState().currentIndex).toBe(0);
    });

    it("completes session when last card is rated", async () => {
      const cards = [makeDueCard("a")];
      mockGetDueCards.mockResolvedValueOnce(cards);
      await useReviewStore.getState().loadDueCards();
      mockReviewSuccess(cards[0]);
      mockGetSessionStats.mockResolvedValueOnce({
        total: 1,
        avg_rating: 3,
        accuracy: 100,
      });

      await useReviewStore.getState().rateCard(3);

      expect(useReviewStore.getState().sessionComplete).toBe(true);
      expect(useReviewStore.getState().sessionStats).toEqual({
        total: 1,
        avg_rating: 3,
        accuracy: 100,
      });
    });
  });

  describe("resetSession", () => {
    it("clears all session state", async () => {
      mockGetDueCards.mockResolvedValueOnce([makeDueCard("a")]);
      await useReviewStore.getState().loadDueCards();

      useReviewStore.getState().resetSession();

      expect(useReviewStore.getState().dueCards).toHaveLength(0);
      expect(useReviewStore.getState().currentIndex).toBe(0);
      expect(useReviewStore.getState().sessionComplete).toBe(false);
      expect(useReviewStore.getState().sessionStats).toBeNull();
    });
  });
});
