import { create } from "zustand";
import type { CardDraft, Card } from "../api/client";
import { extractCards, reviewCard, findDuplicates, getAllCards } from "../api/client";

interface CardState {
  drafts: CardDraft[];
  cards: Card[];
  total: number;
  currentDraftIndex: number;
  duplicates: Card[];
  loading: boolean;
  error: string | null;

  extract: (content: string) => Promise<void>;
  review: (draftId: string, action: "accept" | "edit" | "reject", edits?: Record<string, unknown>) => Promise<void>;
  loadDuplicates: (draftId: string) => Promise<void>;
  loadCards: (page?: number, limit?: number) => Promise<void>;
  nextDraft: () => void;
  prevDraft: () => void;
  clearDrafts: () => void;
}

export const useCardStore = create<CardState>((set, get) => ({
  drafts: [],
  cards: [],
  total: 0,
  currentDraftIndex: 0,
  duplicates: [],
  loading: false,
  error: null,

  extract: async (content) => {
    set({ loading: true, error: null });
    try {
      const { drafts } = await extractCards(content);
      set({ drafts, currentDraftIndex: 0, loading: false });
    } catch (err) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  review: async (draftId, action, edits) => {
    set({ loading: true, error: null });
    try {
      await reviewCard(draftId, action, edits);
      const { drafts } = get();
      const remaining = drafts.filter((d) => d.id !== draftId);
      const newIndex = Math.min(get().currentDraftIndex, Math.max(remaining.length - 1, 0));
      set({ drafts: remaining, currentDraftIndex: newIndex, loading: false });
    } catch (err) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  loadDuplicates: async (draftId) => {
    try {
      const { duplicates } = await findDuplicates(draftId);
      set({ duplicates });
    } catch {
      set({ duplicates: [] });
    }
  },

  loadCards: async (page = 1, limit = 20) => {
    set({ loading: true, error: null });
    try {
      const result = await getAllCards(page, limit);
      set({ cards: result.cards, total: result.total, loading: false });
    } catch (err) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  nextDraft: () =>
    set((s) => ({
      currentDraftIndex: Math.min(s.currentDraftIndex + 1, s.drafts.length - 1),
    })),

  prevDraft: () =>
    set((s) => ({
      currentDraftIndex: Math.max(s.currentDraftIndex - 1, 0),
    })),

  clearDrafts: () =>
    set({ drafts: [], currentDraftIndex: 0, duplicates: [] }),
}));
