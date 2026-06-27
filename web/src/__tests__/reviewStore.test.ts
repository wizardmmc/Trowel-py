import { describe, it, expect, vi, beforeEach } from "vitest";
import { useReviewStore } from "../stores/reviewStore";
import * as client from "../api/client";

// Mock the API client module
vi.mock("../api/client", () => ({
  getDueCards: vi.fn(),
  submitReview: vi.fn(),
  getSessionStats: vi.fn(),
  generateFeynmanQuestion: vi.fn(),
  evaluateFeynmanAnswer: vi.fn(),
}));

const mockGetDueCards = vi.mocked(client.getDueCards);
const mockSubmitReview = vi.mocked(client.submitReview);
const mockGetSessionStats = vi.mocked(client.getSessionStats);
const mockGenerateFeynman = vi.mocked(client.generateFeynmanQuestion);
const mockEvaluateFeynman = vi.mocked(client.evaluateFeynmanAnswer);

beforeEach(() => {
  vi.clearAllMocks();
  // Reset zustand store state between tests
  useReviewStore.setState({
    phase: "idle",
    dueCards: [],
    currentIndex: 0,
    loading: false,
    error: null,
    sessionComplete: false,
    sessionStats: null,
    sessionStartTime: null,
    feynman_phase: "hidden",
    feynman_question: null,
    feynman_result: null,
    feynman_loading: false,
    feynman_error: null,
    _feynmanReqToken: 0,
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

  // ── Feynman overlay state machine ──

  describe("feynman overlay", () => {
    function seedReviewing(cardId = "a") {
      useReviewStore.setState({
        phase: "reviewing",
        dueCards: [makeDueCard(cardId)],
        currentIndex: 0,
      });
    }

    describe("openFeynman", () => {
      it("moves from hidden to prompt", () => {
        seedReviewing();
        useReviewStore.getState().openFeynman();

        const state = useReviewStore.getState();
        expect(state.feynman_phase).toBe("prompt");
      });
    });

    describe("tryFeynman", () => {
      it("generates a question and moves to the question phase", async () => {
        seedReviewing("card-1");
        useReviewStore.setState({ feynman_phase: "prompt" });
        mockGenerateFeynman.mockResolvedValueOnce({
          session_id: "sess1",
          question: "解释闭包",
          hint: "提示",
        });

        await useReviewStore.getState().tryFeynman();

        const state = useReviewStore.getState();
        expect(state.feynman_phase).toBe("question");
        expect(state.feynman_question?.session_id).toBe("sess1");
        expect(state.feynman_question?.question).toBe("解释闭包");
        expect(state.feynman_loading).toBe(false);
        expect(state.feynman_error).toBeNull();
        expect(mockGenerateFeynman).toHaveBeenCalledWith("card-1");
      });

      it("stores the error and stays in prompt on failure", async () => {
        seedReviewing();
        useReviewStore.setState({ feynman_phase: "prompt" });
        mockGenerateFeynman.mockRejectedValueOnce(new Error("LLM 不可用"));

        await useReviewStore.getState().tryFeynman();

        const state = useReviewStore.getState();
        expect(state.feynman_phase).toBe("prompt");
        expect(state.feynman_error).toBe("LLM 不可用");
        expect(state.feynman_loading).toBe(false);
      });
    });

    describe("submitFeynmanAnswer", () => {
      it("evaluates the answer and moves to the feedback phase", async () => {
        seedReviewing();
        useReviewStore.setState({
          feynman_phase: "question",
          feynman_question: {
            session_id: "sess1",
            question: "解释闭包",
            hint: null,
          },
        });
        mockEvaluateFeynman.mockResolvedValueOnce({
          session_id: "sess1",
          accuracy: 80,
          completeness: 60,
          feedback: "基本到位",
          missed_points: ["作用域链"],
        });

        await useReviewStore.getState().submitFeynmanAnswer("我的回答");

        const state = useReviewStore.getState();
        expect(state.feynman_phase).toBe("feedback");
        expect(state.feynman_result?.accuracy).toBe(80);
        expect(state.feynman_result?.missed_points).toEqual(["作用域链"]);
        expect(state.feynman_loading).toBe(false);
        expect(mockEvaluateFeynman).toHaveBeenCalledWith("sess1", "我的回答");
      });

      it("rolls back to question with an error on failure", async () => {
        seedReviewing();
        useReviewStore.setState({
          feynman_phase: "evaluating",
          feynman_question: {
            session_id: "sess1",
            question: "解释闭包",
            hint: null,
          },
        });
        mockEvaluateFeynman.mockRejectedValueOnce(new Error("评估失败"));

        await useReviewStore.getState().submitFeynmanAnswer("回答");

        const state = useReviewStore.getState();
        expect(state.feynman_phase).toBe("question");
        expect(state.feynman_error).toBe("评估失败");
        expect(state.feynman_loading).toBe(false);
      });
    });

    describe("skipFeynman", () => {
      it("hides the overlay and clears question + error", () => {
        seedReviewing();
        useReviewStore.setState({
          feynman_phase: "question",
          feynman_question: {
            session_id: "sess1",
            question: "q",
            hint: null,
          },
          feynman_error: "err",
        });

        useReviewStore.getState().skipFeynman();

        const state = useReviewStore.getState();
        expect(state.feynman_phase).toBe("hidden");
        expect(state.feynman_question).toBeNull();
        expect(state.feynman_error).toBeNull();
      });
    });

    describe("continueFromFeynman", () => {
      it("hides the overlay and clears result", () => {
        seedReviewing();
        useReviewStore.setState({
          feynman_phase: "feedback",
          feynman_result: {
            session_id: "sess1",
            accuracy: 70,
            completeness: 70,
            feedback: "ok",
            missed_points: [],
          },
        });

        useReviewStore.getState().continueFromFeynman();

        const state = useReviewStore.getState();
        expect(state.feynman_phase).toBe("hidden");
        expect(state.feynman_result).toBeNull();
      });
    });
  });
});
