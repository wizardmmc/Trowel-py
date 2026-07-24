import { create } from "zustand";
import type { CardDraft, Card } from "../api/client";
import {
  extractCards,
  extractConversation as extractConversationApi,
  reviewCard,
  findDuplicates,
  getAllCards,
  reExplain as reExplainApi,
} from "../api/client";

export interface ReExplainCandidate {
  readonly id: string;
  /** 界面标签，例如“重写 1”。 */
  readonly tag: string;
  readonly text: string;
}

export const MAX_RE_EXPLAINS = 2;

export const ORIGINAL_ID = "original";

interface CardState {
  drafts: CardDraft[];
  cards: Card[];
  total: number;
  currentDraftIndex: number;
  duplicates: Card[];
  loading: boolean;
  error: string | null;

  reExplainRegens: ReExplainCandidate[];
  reExplainSelectedId: string;
  reExplainLoading: boolean;
  reExplainError: string | null;

  extract: (content: string) => Promise<void>;
  extractConversation: (content: string) => Promise<void>;
  review: (
    draftId: string,
    action: "accept" | "edit" | "reject",
    edits?: Record<string, unknown>
  ) => Promise<void>;
  loadDuplicates: (draftId: string) => Promise<void>;
  loadCards: (page?: number, limit?: number) => Promise<void>;
  nextDraft: () => void;
  prevDraft: () => void;
  clearDrafts: () => void;

  regenerateExplanation: (draft: CardDraft, hint?: string) => Promise<void>;
  selectReExplain: (id: string) => void;
  resetReExplain: () => void;
}

const RE_EXPLAIN_RESET = {
  reExplainRegens: [] as ReExplainCandidate[],
  reExplainSelectedId: ORIGINAL_ID,
  reExplainLoading: false,
  reExplainError: null as string | null,
};

export const useCardStore = create<CardState>((set, get) => ({
  drafts: [],
  cards: [],
  total: 0,
  currentDraftIndex: 0,
  duplicates: [],
  loading: false,
  error: null,
  ...RE_EXPLAIN_RESET,

  extract: async (content) => {
    set({ loading: true, error: null });
    try {
      const { drafts } = await extractCards(content);
      set({ drafts, currentDraftIndex: 0, loading: false, ...RE_EXPLAIN_RESET });
    } catch (err) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  extractConversation: async (content) => {
    set({ loading: true, error: null });
    try {
      const { drafts } = await extractConversationApi(content);
      set({ drafts, currentDraftIndex: 0, loading: false, ...RE_EXPLAIN_RESET });
    } catch (err) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  review: async (draftId, action, edits) => {
    set({ loading: true, error: null });
    try {
      await reviewCard(draftId, action, edits);
      set((s) => {
        const remaining = s.drafts.filter((d) => d.id !== draftId);
        const newIndex = Math.min(
          s.currentDraftIndex,
          Math.max(remaining.length - 1, 0)
        );
        return {
          drafts: remaining,
          currentDraftIndex: newIndex,
          loading: false,
          ...RE_EXPLAIN_RESET,
        };
      });
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
      ...RE_EXPLAIN_RESET,
    })),

  prevDraft: () =>
    set((s) => ({
      currentDraftIndex: Math.max(s.currentDraftIndex - 1, 0),
      ...RE_EXPLAIN_RESET,
    })),

  clearDrafts: () =>
    set({ drafts: [], currentDraftIndex: 0, duplicates: [], ...RE_EXPLAIN_RESET }),

  regenerateExplanation: async (draft, hint) => {
    if (
      get().reExplainLoading ||
      get().reExplainRegens.length >= MAX_RE_EXPLAINS
    ) {
      return;
    }
    set({ reExplainLoading: true, reExplainError: null });
    try {
      const count = get().reExplainRegens.length;
      const { explanation } = await reExplainApi(
        draft.explanation,
        draft.title,
        draft.category,
        hint
      );
      const id = `regen-${count + 1}`;
      set((s) => ({
        reExplainRegens: [
          ...s.reExplainRegens,
          { id, tag: `重写 ${count + 1}`, text: explanation },
        ],
        reExplainLoading: false,
      }));
    } catch (err) {
      set({ reExplainError: (err as Error).message, reExplainLoading: false });
    }
  },

  selectReExplain: (id) => set({ reExplainSelectedId: id }),

  resetReExplain: () => set(RE_EXPLAIN_RESET),
}));
