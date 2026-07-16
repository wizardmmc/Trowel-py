import { describe, it, expect, vi, beforeEach } from "vitest";
import { useSuggestionsStore } from "../stores/suggestionsStore";
import type { Suggestion } from "../api/client";

// Mock the HTTP layer; the store under test orchestrates state, not network.
vi.mock("../api/client", () => ({
  getSuggestions: vi.fn(),
  patchSuggestionStatus: vi.fn(),
}));

import {
  getSuggestions as getSuggestionsApi,
  patchSuggestionStatus as patchApi,
} from "../api/client";

const sug = (over: Partial<Suggestion> = {}): Suggestion => ({
  id: "s1",
  dimension: "ability",
  body: "会 FastAPI",
  sources: ["sess-abc"],
  date: "2026-07-14",
  status: "pending",
  ...over,
});

describe("suggestionsStore", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useSuggestionsStore.setState({
      suggestions: [],
      loading: false,
      error: null,
    });
  });

  it("fetchSuggestions loads pending suggestions", async () => {
    vi.mocked(getSuggestionsApi).mockResolvedValue([sug()]);

    await useSuggestionsStore.getState().fetchSuggestions();

    expect(useSuggestionsStore.getState().suggestions).toHaveLength(1);
    expect(useSuggestionsStore.getState().error).toBeNull();
    expect(useSuggestionsStore.getState().loading).toBe(false);
  });

  it("fetchSuggestions stores the error message on failure", async () => {
    vi.mocked(getSuggestionsApi).mockRejectedValue(new Error("boom"));

    await useSuggestionsStore.getState().fetchSuggestions();

    expect(useSuggestionsStore.getState().suggestions).toEqual([]);
    expect(useSuggestionsStore.getState().error).toBe("boom");
  });

  it("patchStatus calls the api and drops the suggestion locally", async () => {
    vi.mocked(patchApi).mockResolvedValue(undefined);
    useSuggestionsStore.setState({
      suggestions: [sug({ id: "s1" }), sug({ id: "s2", body: "second" })],
    });

    await useSuggestionsStore.getState().patchStatus("s1", "accepted");

    expect(patchApi).toHaveBeenCalledWith("s1", "accepted");
    expect(useSuggestionsStore.getState().suggestions.map((s) => s.id)).toEqual([
      "s2",
    ]);
  });

  it("patchStatus rethrows on failure and keeps the suggestion", async () => {
    vi.mocked(patchApi).mockRejectedValue(new Error("nope"));
    useSuggestionsStore.setState({ suggestions: [sug()] });

    await expect(
      useSuggestionsStore.getState().patchStatus("s1", "discarded"),
    ).rejects.toThrow("nope");

    // not dropped on failure — the user can retry
    expect(useSuggestionsStore.getState().suggestions).toHaveLength(1);
  });
});
