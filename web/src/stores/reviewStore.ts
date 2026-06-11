import { create } from "zustand";
import {
  getDueCards,
  submitReview,
  getSessionStats,
  type DueCard,
  type SessionStats,
} from "../api/client";

interface ReviewState {
  dueCards: DueCard[];
  currentIndex: number;
  loading: boolean;
  error: string | null;
  sessionComplete: boolean;
  sessionStats: SessionStats | null;
  sessionStartTime: string | null;

  loadDueCards: () => Promise<void>;
  rateCard: (rating: number) => Promise<void>;
  resetSession: () => void;
}

export const useReviewStore = create<ReviewState>((set, get) => ({
  dueCards: [],
  currentIndex: 0,
  loading: false,
  error: null,
  sessionComplete: false,
  sessionStats: null,
  sessionStartTime: null,

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

      const remaining = dueCards.filter(
        (_, i) => i !== currentIndex,
      );

      if (remaining.length === 0) {
        // Session complete — fetch stats
        const since = sessionStartTime ?? new Date().toISOString();
        const stats = await getSessionStats(since);
        set({
          dueCards: [],
          currentIndex: 0,
          loading: false,
          sessionComplete: true,
          sessionStats: stats,
        });
      } else {
        const newIndex = Math.min(
          currentIndex,
          remaining.length - 1,
        );
        set({
          dueCards: remaining,
          currentIndex: newIndex,
          loading: false,
        });
      }
    } catch (err) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  resetSession: () =>
    set({
      dueCards: [],
      currentIndex: 0,
      loading: false,
      error: null,
      sessionComplete: false,
      sessionStats: null,
      sessionStartTime: null,
    }),
}));
