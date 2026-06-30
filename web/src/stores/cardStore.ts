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

/** a regenerated explanation candidate. the original (V0) is NOT stored here —
 *  it is always the draft's own explanation, rendered separately. */
export interface ReExplainCandidate {
  /** "regen-1" / "regen-2" — also used as the selected-id value */
  readonly id: string;
  /** display label, e.g. "重写 1" */
  readonly tag: string;
  /** the regenerated explanation text */
  readonly text: string;
}

/** max regenerations per draft in one review session (slice 021 invariant 3). */
export const MAX_RE_EXPLAINS = 2;

/** selected-id value that represents the draft's original explanation. */
export const ORIGINAL_ID = "original";

interface CardState {
  drafts: CardDraft[];
  cards: Card[];
  total: number;
  currentDraftIndex: number;
  duplicates: Card[];
  loading: boolean;
  error: string | null;

  /** regenerated candidates for the draft under review (original not included). */
  reExplainRegens: ReExplainCandidate[];
  /** which candidate is selected: ORIGINAL_ID (default) or a regen id. */
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

  /** regenerate the given draft's explanation from a different angle. no-op once
   *  MAX_RE_EXPLAINS regens already exist (frontend enforces the cap). */
  regenerateExplanation: (draft: CardDraft, hint?: string) => Promise<void>;
  /** mark a candidate (original or a regen) as the selected one. */
  selectReExplain: (id: string) => void;
  /** clear all regens and re-select the original. */
  resetReExplain: () => void;
}

/** shape reused on every draft switch / review / extract / clear so the
 *  candidate pool never leaks across drafts (invariant 2: candidates are
 *  per-session, in-memory only). */
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
    // block concurrent calls AND cap: the UI disables the button while loading,
    // but enforce it in the store too so id sequencing stays correct under rapid
    // clicks (a second call before re-render would otherwise reuse count=0 and
    // produce a duplicate "regen-1" id).
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
