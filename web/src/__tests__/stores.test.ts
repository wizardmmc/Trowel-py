import { describe, it, expect, vi, beforeEach } from "vitest";
import { useCardStore } from "../stores/cardStore";
import * as api from "../api/client";

vi.mock("../api/client");

describe("cardStore", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useCardStore.setState({
      drafts: [],
      cards: [],
      total: 0,
      currentDraftIndex: 0,
      duplicates: [],
      loading: false,
      error: null,
    });
  });

  it("extract sets drafts on success", async () => {
    const mockDrafts = [
      { id: "1", title: "A", category: "c", explanation: "e", example: null, difficulty: 3, tags: [], confidence: 4, source_type: "chat", source: null },
    ];
    vi.mocked(api.extractCards).mockResolvedValue({ drafts: mockDrafts });

    await useCardStore.getState().extract("content");

    expect(useCardStore.getState().drafts).toHaveLength(1);
    expect(useCardStore.getState().loading).toBe(false);
  });

  it("extract sets error on failure", async () => {
    vi.mocked(api.extractCards).mockRejectedValue(new Error("Network fail"));

    await useCardStore.getState().extract("content");

    expect(useCardStore.getState().error).toBe("Network fail");
    expect(useCardStore.getState().loading).toBe(false);
  });

  it("review removes draft from list", async () => {
    useCardStore.setState({
      drafts: [
        { id: "1", title: "A", category: "c", explanation: "e", example: null, difficulty: 3, tags: [], confidence: 4, source_type: "chat", source: null },
        { id: "2", title: "B", category: "c", explanation: "e", example: null, difficulty: 3, tags: [], confidence: 4, source_type: "chat", source: null },
      ],
    });
    vi.mocked(api.reviewCard).mockResolvedValue({ card: { id: "1" } as api.Card });

    await useCardStore.getState().review("1", "accept");

    expect(useCardStore.getState().drafts).toHaveLength(1);
    expect(useCardStore.getState().drafts[0].id).toBe("2");
  });

  it("nextDraft/prevDraft clamp index", () => {
    useCardStore.setState({
      drafts: [
        { id: "1", title: "A", category: "c", explanation: "e", example: null, difficulty: 3, tags: [], confidence: 4, source_type: "chat", source: null },
      ],
      currentDraftIndex: 0,
    });

    useCardStore.getState().nextDraft();
    expect(useCardStore.getState().currentDraftIndex).toBe(0);

    useCardStore.getState().prevDraft();
    expect(useCardStore.getState().currentDraftIndex).toBe(0);
  });

  it("clearDrafts resets state", () => {
    useCardStore.setState({
      drafts: [
        { id: "1", title: "A", category: "c", explanation: "e", example: null, difficulty: 3, tags: [], confidence: 4, source_type: "chat", source: null },
      ],
      currentDraftIndex: 0,
    });

    useCardStore.getState().clearDrafts();

    expect(useCardStore.getState().drafts).toHaveLength(0);
    expect(useCardStore.getState().currentDraftIndex).toBe(0);
  });
});
