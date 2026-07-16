import { create } from "zustand";
import {
  getSuggestions as getSuggestionsApi,
  patchSuggestionStatus as patchSuggestionApi,
  type Suggestion,
} from "../api/client";

export interface SuggestionsState {
  /** pending AI suggestions for the user to review (empty until first fetch) */
  readonly suggestions: readonly Suggestion[];
  readonly loading: boolean;
  readonly error: string | null;

  fetchSuggestions: () => Promise<void>;
  /** flip one suggestion's status (accept / discard). On success, drops it from
   * the local list (it's resolved). Rethrows on failure so the modal can react. */
  patchStatus: (id: string, status: "accepted" | "discarded") => Promise<void>;
}

export const useSuggestionsStore = create<SuggestionsState>((set) => ({
  suggestions: [],
  loading: false,
  error: null,

  fetchSuggestions: async () => {
    set({ loading: true, error: null });
    try {
      const suggestions = await getSuggestionsApi();
      set({ suggestions, loading: false });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to load suggestions";
      set({ loading: false, error: message });
    }
  },

  patchStatus: async (id, status) => {
    await patchSuggestionApi(id, status);
    // the suggestion is resolved → drop it from the local list (the back-end
    // already flipped its status, so it won't come back on the next fetch).
    set((state) => ({
      suggestions: state.suggestions.filter((s) => s.id !== id),
    }));
  },
}));
