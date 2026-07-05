import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  generateFeynmanQuestion,
  evaluateFeynmanAnswer,
  getFeynmanHistory,
  type FeynmanQuestion,
  type FeynmanEvaluation,
  type FeynmanHistoryItem,
} from "../api/client";

// Mock global fetch — same pattern as review-api-client.test.ts
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

/** Build a successful {success, data, error} envelope response */
function mockResponse<T>(data: T, success = true, error: string | null = null) {
  return {
    ok: true,
    json: () => Promise.resolve({ success, data, error }),
  };
}

/** Build a non-2xx HTTP response (e.g. FastAPI 422 validation) */
function mockHttpError(status: number) {
  return {
    ok: false,
    status,
    json: () => Promise.resolve({ success: false, data: null, error: null }),
  };
}

beforeEach(() => {
  mockFetch.mockReset();
});

describe("generateFeynmanQuestion", () => {
  it("posts card_id and returns the generated question", async () => {
    const question: FeynmanQuestion = {
      session_id: "sess1",
      question: "用你自己的话解释什么是闭包",
      hint: "想想函数和变量的关系",
    };
    mockFetch.mockResolvedValueOnce(mockResponse(question));

    const result = await generateFeynmanQuestion("card1");

    expect(result.session_id).toBe("sess1");
    expect(result.question).toContain("闭包");
    expect(result.hint).toContain("函数");
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/feynman/generate",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ card_id: "card1" }),
      }),
    );
  });

  it("accepts a null hint", async () => {
    const question: FeynmanQuestion = {
      session_id: "sess2",
      question: "解释 X",
      hint: null,
    };
    mockFetch.mockResolvedValueOnce(mockResponse(question));

    const result = await generateFeynmanQuestion("card1");
    expect(result.hint).toBeNull();
  });

  it("throws the server error when success=false (card not found)", async () => {
    mockFetch.mockResolvedValueOnce(mockResponse(null, false, "Card not found"));

    await expect(generateFeynmanQuestion("missing")).rejects.toThrow(
      "Card not found",
    );
  });

  it("throws on HTTP error (422 validation)", async () => {
    mockFetch.mockResolvedValueOnce(mockHttpError(422));

    await expect(generateFeynmanQuestion("")).rejects.toThrow("API error: 422");
  });
});

describe("evaluateFeynmanAnswer", () => {
  it("posts session_id + answer and returns the scores", async () => {
    const evaluation: FeynmanEvaluation = {
      session_id: "sess1",
      accuracy: 80,
      completeness: 60,
      feedback: "基本到位，但漏了作用域。",
      missed_points: ["作用域链", "变量生命周期"],
    };
    mockFetch.mockResolvedValueOnce(mockResponse(evaluation));

    const result = await evaluateFeynmanAnswer("sess1", "闭包就是...");

    expect(result.accuracy).toBe(80);
    expect(result.completeness).toBe(60);
    expect(result.missed_points).toHaveLength(2);
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/feynman/evaluate",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ session_id: "sess1", answer: "闭包就是..." }),
      }),
    );
  });

  it("accepts an empty missed_points list", async () => {
    const evaluation: FeynmanEvaluation = {
      session_id: "sess1",
      accuracy: 100,
      completeness: 100,
      feedback: "完美",
      missed_points: [],
    };
    mockFetch.mockResolvedValueOnce(mockResponse(evaluation));

    const result = await evaluateFeynmanAnswer("sess1", "ans");
    expect(result.missed_points).toEqual([]);
  });

  it("throws the server error when session not found", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse(null, false, "Session not found"),
    );

    await expect(
      evaluateFeynmanAnswer("missing", "ans"),
    ).rejects.toThrow("Session not found");
  });
});

describe("getFeynmanHistory", () => {
  it("fetches the session history for a card", async () => {
    const history: FeynmanHistoryItem[] = [
      {
        id: "sess1",
        card_id: "card1",
        question: "解释 X",
        user_answer: "X 是...",
        accuracy: 70,
        completeness: 50,
        feedback: "...",
        missed_points: ["a"],
        created_at: "2026-01-01T00:00:00",
      },
    ];
    mockFetch.mockResolvedValueOnce(mockResponse(history));

    const result = await getFeynmanHistory("card1");

    expect(result).toHaveLength(1);
    expect(result[0].id).toBe("sess1");
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/feynman/history/card1",
      undefined,
    );
  });

  it("returns an empty array when no history exists", async () => {
    mockFetch.mockResolvedValueOnce(mockResponse([]));

    const result = await getFeynmanHistory("card1");
    expect(result).toEqual([]);
  });

  it("treats an unevaluated session's null scores correctly", async () => {
    const history: FeynmanHistoryItem[] = [
      {
        id: "sess-pending",
        card_id: "card1",
        question: "解释 Y",
        user_answer: null,
        accuracy: null,
        completeness: null,
        feedback: null,
        missed_points: null,
        created_at: null,
      },
    ];
    mockFetch.mockResolvedValueOnce(mockResponse(history));

    const result = await getFeynmanHistory("card1");
    expect(result[0].accuracy).toBeNull();
    expect(result[0].missed_points).toBeNull();
  });
});
