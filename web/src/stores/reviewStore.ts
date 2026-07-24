import { create } from "zustand";
import {
  getDueCards,
  submitReview,
  getSessionStats,
  generateFeynmanQuestion,
  evaluateFeynmanAnswer,
  type DueCard,
  type SessionStats,
  type FeynmanQuestion,
  type FeynmanEvaluation,
} from "../api/client";

export type ReviewPhase = "idle" | "reviewing" | "complete";

export type FeynmanPhase =
  | "hidden"
  | "prompt"
  | "question"
  | "evaluating"
  | "feedback";

const FEYNMAN_HIDDEN = {
  feynman_phase: "hidden" as FeynmanPhase,
  feynman_question: null as FeynmanQuestion | null,
  feynman_result: null as FeynmanEvaluation | null,
  feynman_loading: false,
  feynman_error: null as string | null,
};

interface ReviewState {
  phase: ReviewPhase;
  dueCards: DueCard[];
  currentIndex: number;
  loading: boolean;
  error: string | null;
  sessionComplete: boolean;
  sessionStats: SessionStats | null;
  sessionStartTime: string | null;

  feynman_phase: FeynmanPhase;
  feynman_question: FeynmanQuestion | null;
  feynman_result: FeynmanEvaluation | null;
  feynman_loading: boolean;
  feynman_error: string | null;
  /** 请求代次变化后，旧响应不得覆盖当前卡片。 */
  _feynmanReqToken: number;

  startSession: () => Promise<void>;
  loadDueCards: () => Promise<void>;
  rateCard: (rating: number) => Promise<void>;
  resetSession: () => void;

  openFeynman: () => void;
  tryFeynman: () => Promise<void>;
  submitFeynmanAnswer: (answer: string) => Promise<void>;
  skipFeynman: () => void;
  continueFromFeynman: () => void;
}

function hideAndBump(state: ReviewState) {
  return { ...FEYNMAN_HIDDEN, _feynmanReqToken: state._feynmanReqToken + 1 };
}

export const useReviewStore = create<ReviewState>((set, get) => ({
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

  startSession: async () => {
    set({ loading: true, error: null });
    try {
      const cards = await getDueCards();
      if (cards.length === 0) {
        set({
          phase: "idle",
          dueCards: [],
          loading: false,
          sessionComplete: false,
          sessionStats: null,
        });
        return;
      }
      set({
        phase: "reviewing",
        dueCards: cards,
        currentIndex: 0,
        loading: false,
        sessionComplete: false,
        sessionStats: null,
        sessionStartTime: new Date().toISOString(),
      });
    } catch (err) {
      set({ error: (err as Error).message, loading: false, phase: "idle" });
    }
  },

  loadDueCards: async () => {
    set({ loading: true, error: null });
    try {
      const cards = await getDueCards();
      if (cards.length === 0) {
        set({
          dueCards: [],
          loading: false,
          sessionComplete: true,
          sessionStartTime: null,
        });
        return;
      }
      set({
        dueCards: cards,
        currentIndex: 0,
        loading: false,
        sessionComplete: false,
        sessionStartTime: new Date().toISOString(),
      });
    } catch (err) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  rateCard: async (rating: number) => {
    const { dueCards, currentIndex, sessionStartTime } = get();
    const currentCard = dueCards[currentIndex];
    if (!currentCard) return;

    set({ loading: true, error: null });
    try {
      await submitReview(currentCard.card.id, rating);

      const remaining = dueCards.filter((_, i) => i !== currentIndex);

      if (remaining.length === 0) {
        const since = sessionStartTime ?? new Date().toISOString();
        const stats = await getSessionStats(since);
        set({
          phase: "complete",
          dueCards: [],
          currentIndex: 0,
          loading: false,
          sessionComplete: true,
          sessionStats: stats,
          ...hideAndBump(get()),
        });
      } else {
        const newIndex = Math.min(currentIndex, remaining.length - 1);
        set({
          dueCards: remaining,
          currentIndex: newIndex,
          loading: false,
          ...hideAndBump(get()),
        });
      }
    } catch (err) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  resetSession: () =>
    set({
      phase: "idle",
      dueCards: [],
      currentIndex: 0,
      loading: false,
      error: null,
      sessionComplete: false,
      sessionStats: null,
      sessionStartTime: null,
      ...FEYNMAN_HIDDEN,
      _feynmanReqToken: 0,
    }),


  openFeynman: () => set({ feynman_phase: "prompt", feynman_error: null }),

  tryFeynman: async () => {
    const state = get();
    if (state.feynman_loading) return;
    const currentCard = state.dueCards[state.currentIndex];
    if (!currentCard) return;
    const token = state._feynmanReqToken + 1;
    set({ feynman_loading: true, feynman_error: null, _feynmanReqToken: token });
    try {
      const question = await generateFeynmanQuestion(currentCard.card.id);
      if (get()._feynmanReqToken !== token) return;
      set({
        feynman_phase: "question",
        feynman_question: question,
        feynman_loading: false,
      });
    } catch (err) {
      if (get()._feynmanReqToken !== token) return;
      set({
        feynman_loading: false,
        feynman_error: (err as Error).message,
      });
    }
  },

  submitFeynmanAnswer: async (answer: string) => {
    const state = get();
    if (state.feynman_loading) return;
    if (!state.feynman_question) return;
    const token = state._feynmanReqToken + 1;
    set({
      feynman_phase: "evaluating",
      feynman_loading: true,
      feynman_error: null,
      _feynmanReqToken: token,
    });
    try {
      const result = await evaluateFeynmanAnswer(
        state.feynman_question.session_id,
        answer,
      );
      if (get()._feynmanReqToken !== token) return;
      set({
        feynman_phase: "feedback",
        feynman_result: result,
        feynman_loading: false,
      });
    } catch (err) {
      if (get()._feynmanReqToken !== token) return;
      set({
        feynman_phase: "question",
        feynman_loading: false,
        feynman_error: (err as Error).message,
      });
    }
  },

  skipFeynman: () => set(hideAndBump),

  continueFromFeynman: () => set(hideAndBump),
}));
