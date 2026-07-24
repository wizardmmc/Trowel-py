import { beforeEach, vi } from "vitest";

vi.mock("../api/client", () => ({
  getDueCards: vi.fn(),
  submitReview: vi.fn(),
  getSessionStats: vi.fn(),
  generateFeynmanQuestion: vi.fn(),
  evaluateFeynmanAnswer: vi.fn(),
}));

import * as client from "../api/client";
import { useReviewStore } from "../stores/reviewStore";

export const mockGetDueCards = vi.mocked(client.getDueCards);
export const mockSubmitReview = vi.mocked(client.submitReview);
export const mockGetSessionStats = vi.mocked(client.getSessionStats);
export const mockGenerateFeynman = vi.mocked(client.generateFeynmanQuestion);
export const mockEvaluateFeynman = vi.mocked(client.evaluateFeynmanAnswer);

export function makeDueCard(id: string): client.DueCard {
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
      stability: 0,
      difficulty: 0,
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

export function seedReviewing(cardId = "a") {
  useReviewStore.setState({
    phase: "reviewing",
    dueCards: [makeDueCard(cardId)],
    currentIndex: 0,
  });
}

export function mockReviewSuccess(card: client.DueCard) {
  mockSubmitReview.mockResolvedValueOnce({
    card: card.card,
    fsrs_state: card.fsrs_state,
    review_log: {
      id: "log1",
      card_id: card.card.id,
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
}

beforeEach(() => {
  vi.clearAllMocks();
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
