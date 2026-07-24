import { create } from "zustand";
import {
  getSuggestions as getSuggestionsApi,
  patchSuggestionStatus as patchSuggestionApi,
  type Suggestion,
} from "../api/client";

export interface SuggestionsState {
  readonly suggestions: readonly Suggestion[];
  readonly loading: boolean;
  readonly error: string | null;

  fetchSuggestions: () => Promise<void>;
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
    set((state) => ({
      suggestions: state.suggestions.filter((s) => s.id !== id),
    }));
  },
}));
