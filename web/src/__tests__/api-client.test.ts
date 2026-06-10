import { describe, it, expect, vi, beforeEach } from "vitest";
import { extractCards, reviewCard, getAllCards } from "../api/client";

describe("api/client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("extractCards sends POST and returns drafts", async () => {
    const mockDrafts = [
      { id: "1", title: "Test", category: "concept", explanation: "exp", example: null, difficulty: 3, tags: [], confidence: 4, source_type: "chat", source: null },
    ];

    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ success: true, data: { drafts: mockDrafts }, error: null }))
    );

    const result = await extractCards("some content");
    expect(result.drafts).toHaveLength(1);
    expect(result.drafts[0].title).toBe("Test");

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/cards/extract",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("reviewCard sends POST with action", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ success: true, data: { card: { id: "1" } }, error: null }))
    );

    await reviewCard("draft-1", "accept");
    const call = vi.spyOn(globalThis, "fetch").mock.calls[0];
    const body = JSON.parse((call[1] as RequestInit).body as string);

    expect(body.action).toBe("accept");
  });

  it("getAllCards sends GET with pagination", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ success: true, data: { cards: [], total: 0, page: 1, limit: 20 }, error: null }))
    );

    await getAllCards(2, 10);
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/cards?page=2&limit=10",
      undefined
    );
  });

  it("throws on API error response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ success: false, data: null, error: "Draft not found" }))
    );

    await expect(reviewCard("bad-id", "accept")).rejects.toThrow("Draft not found");
  });

  it("throws on HTTP error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 500 })
    );

    await expect(extractCards("x")).rejects.toThrow("API error: 500");
  });
});
