import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  useCardStore,
  MAX_RE_EXPLAINS,
  ORIGINAL_ID,
} from "../stores/cardStore";
import type { CardDraft } from "../api/client";
import * as client from "../api/client";

// Mock the API client module — store actions must not hit the network.
vi.mock("../api/client", () => ({
  extractCards: vi.fn(),
  extractConversation: vi.fn(),
  reviewCard: vi.fn(),
  findDuplicates: vi.fn(),
  getAllCards: vi.fn(),
  reExplain: vi.fn(),
}));

const mockReExplain = vi.mocked(client.reExplain);
const mockReviewCard = vi.mocked(client.reviewCard);

function makeDraft(id: string): CardDraft {
  return {
    id,
    title: `title-${id}`,
    category: "cat",
    explanation: "original explanation long enough",
    example: null,
    difficulty: 3,
    tags: [],
    confidence: 4,
    source_type: "chat",
    source: null,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  useCardStore.setState({
    drafts: [],
    cards: [],
    total: 0,
    currentDraftIndex: 0,
    duplicates: [],
    loading: false,
    error: null,
    reExplainRegens: [],
    reExplainSelectedId: ORIGINAL_ID,
    reExplainLoading: false,
    reExplainError: null,
  });
});

describe("cardStore re-explain state machine", () => {
  it("defaults to ORIGINAL_ID — the draft's own explanation is pre-selected", () => {
    expect(useCardStore.getState().reExplainSelectedId).toBe(ORIGINAL_ID);
    expect(useCardStore.getState().reExplainRegens).toHaveLength(0);
  });

  it("regenerateExplanation appends a regen candidate via the API", async () => {
    mockReExplain.mockResolvedValue({ explanation: "a fresh angle" });
    const draft = makeDraft("d1");
    await useCardStore.getState().regenerateExplanation(draft);

    const { reExplainRegens } = useCardStore.getState();
    expect(reExplainRegens).toHaveLength(1);
    expect(reExplainRegens[0]).toEqual({
      id: "regen-1",
      tag: "重写 1",
      text: "a fresh angle",
    });
    expect(mockReExplain).toHaveBeenCalledWith(
      draft.explanation,
      draft.title,
      draft.category,
      undefined,
    );
  });

  it("passes the hint through when given", async () => {
    mockReExplain.mockResolvedValue({ explanation: "x" });
    const draft = makeDraft("d1");
    await useCardStore.getState().regenerateExplanation(draft, "更通俗");
    expect(mockReExplain).toHaveBeenCalledWith(
      draft.explanation,
      draft.title,
      draft.category,
      "更通俗",
    );
  });

  it("caps regeneration at MAX_RE_EXPLAINS and stops calling the API", async () => {
    mockReExplain.mockResolvedValue({ explanation: "x" });
    const draft = makeDraft("d1");
    const { regenerateExplanation } = useCardStore.getState();
    await regenerateExplanation(draft);
    await regenerateExplanation(draft);
    await regenerateExplanation(draft); // 3rd — must be a no-op (invariant 3)

    expect(useCardStore.getState().reExplainRegens).toHaveLength(MAX_RE_EXPLAINS);
    expect(mockReExplain).toHaveBeenCalledTimes(MAX_RE_EXPLAINS);
  });

  it("records reExplainError when the API fails, without adding a candidate", async () => {
    mockReExplain.mockRejectedValue(new Error("boom"));
    await useCardStore.getState().regenerateExplanation(makeDraft("d1"));
    const s = useCardStore.getState();
    expect(s.reExplainRegens).toHaveLength(0);
    expect(s.reExplainError).toBe("boom");
    expect(s.reExplainLoading).toBe(false);
  });

  it("selectReExplain changes the selected id", () => {
    useCardStore.setState({
      reExplainRegens: [{ id: "regen-1", tag: "重写 1", text: "x" }],
    });
    useCardStore.getState().selectReExplain("regen-1");
    expect(useCardStore.getState().reExplainSelectedId).toBe("regen-1");
  });

  it("resetReExplain clears regens and re-selects the original", async () => {
    mockReExplain.mockResolvedValue({ explanation: "x" });
    const { regenerateExplanation, resetReExplain } = useCardStore.getState();
    await regenerateExplanation(makeDraft("d1"));
    useCardStore.getState().selectReExplain("regen-1");

    resetReExplain();
    const s = useCardStore.getState();
    expect(s.reExplainRegens).toHaveLength(0);
    expect(s.reExplainSelectedId).toBe(ORIGINAL_ID);
  });

  it("switching drafts (nextDraft) wipes the candidate pool", async () => {
    mockReExplain.mockResolvedValue({ explanation: "x" });
    useCardStore.setState({
      drafts: [makeDraft("d1"), makeDraft("d2")],
      currentDraftIndex: 0,
    });
    await useCardStore
      .getState()
      .regenerateExplanation(useCardStore.getState().drafts[0]);
    expect(useCardStore.getState().reExplainRegens).toHaveLength(1);

    useCardStore.getState().nextDraft();
    expect(useCardStore.getState().reExplainRegens).toHaveLength(0);
    expect(useCardStore.getState().reExplainSelectedId).toBe(ORIGINAL_ID);
  });

  it("review wipes the candidate pool (the draft is gone)", async () => {
    mockReExplain.mockResolvedValue({ explanation: "x" });
    // review doesn't read the return value — only the state reset matters here
    mockReviewCard.mockResolvedValue({} as never);
    useCardStore.setState({ drafts: [makeDraft("d1")], currentDraftIndex: 0 });
    await useCardStore
      .getState()
      .regenerateExplanation(useCardStore.getState().drafts[0]);

    await useCardStore.getState().review("d1", "accept");
    expect(useCardStore.getState().reExplainRegens).toHaveLength(0);
  });

  it("no-ops while a regeneration is already in flight (concurrency guard)", async () => {
    let resolveFirst: (v: { explanation: string }) => void = () => {};
    mockReExplain.mockReturnValue(
      new Promise<{ explanation: string }>((res) => {
        resolveFirst = res;
      }),
    );
    const draft = makeDraft("d1");
    const { regenerateExplanation } = useCardStore.getState();

    const first = regenerateExplanation(draft); // pending → loading=true
    await regenerateExplanation(draft); // must no-op (loading), not add regen-2

    expect(useCardStore.getState().reExplainLoading).toBe(true);
    expect(mockReExplain).toHaveBeenCalledTimes(1);

    resolveFirst({ explanation: "done" });
    await first;
    expect(useCardStore.getState().reExplainRegens).toHaveLength(1);
    expect(useCardStore.getState().reExplainRegens[0].id).toBe("regen-1");
  });

  it("regenerating does not steal the user's explicit selection", async () => {
    mockReExplain.mockResolvedValue({ explanation: "x" });
    const draft = makeDraft("d1");
    const { regenerateExplanation } = useCardStore.getState();
    await regenerateExplanation(draft);
    useCardStore.getState().selectReExplain("regen-1");

    await regenerateExplanation(draft); // produces regen-2

    expect(useCardStore.getState().reExplainRegens).toHaveLength(2);
    expect(useCardStore.getState().reExplainSelectedId).toBe("regen-1");
  });
});
