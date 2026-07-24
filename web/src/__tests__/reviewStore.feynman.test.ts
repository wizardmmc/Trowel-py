import { describe, expect, it } from "vitest";
import {
  mockEvaluateFeynman,
  mockGenerateFeynman,
  seedReviewing,
} from "./reviewStoreTestHarness";
import { useReviewStore } from "../stores/reviewStore";

describe("reviewStore feynman overlay", () => {
  describe("openFeynman", () => {
    it("moves from hidden to prompt", () => {
      seedReviewing();
      useReviewStore.getState().openFeynman();
      expect(useReviewStore.getState().feynman_phase).toBe("prompt");
    });
  });

  describe("tryFeynman", () => {
    it("generates a question and moves to the question phase", async () => {
      seedReviewing("card-1");
      useReviewStore.setState({ feynman_phase: "prompt" });
      mockGenerateFeynman.mockResolvedValueOnce({
        session_id: "sess1",
        question: "解释闭包",
        hint: "提示",
      });

      await useReviewStore.getState().tryFeynman();

      expect(useReviewStore.getState().feynman_phase).toBe("question");
      expect(useReviewStore.getState().feynman_question?.session_id).toBe("sess1");
      expect(useReviewStore.getState().feynman_question?.question).toBe("解释闭包");
      expect(useReviewStore.getState().feynman_loading).toBe(false);
      expect(useReviewStore.getState().feynman_error).toBeNull();
      expect(mockGenerateFeynman).toHaveBeenCalledWith("card-1");
    });

    it("stores the error and stays in prompt on failure", async () => {
      seedReviewing();
      useReviewStore.setState({ feynman_phase: "prompt" });
      mockGenerateFeynman.mockRejectedValueOnce(new Error("LLM 不可用"));

      await useReviewStore.getState().tryFeynman();

      expect(useReviewStore.getState().feynman_phase).toBe("prompt");
      expect(useReviewStore.getState().feynman_error).toBe("LLM 不可用");
      expect(useReviewStore.getState().feynman_loading).toBe(false);
    });
  });

  describe("submitFeynmanAnswer", () => {
    it("evaluates the answer and moves to the feedback phase", async () => {
      seedReviewing();
      useReviewStore.setState({
        feynman_phase: "question",
        feynman_question: {
          session_id: "sess1",
          question: "解释闭包",
          hint: null,
        },
      });
      mockEvaluateFeynman.mockResolvedValueOnce({
        session_id: "sess1",
        accuracy: 80,
        completeness: 60,
        feedback: "基本到位",
        missed_points: ["作用域链"],
      });

      await useReviewStore.getState().submitFeynmanAnswer("我的回答");

      expect(useReviewStore.getState().feynman_phase).toBe("feedback");
      expect(useReviewStore.getState().feynman_result?.accuracy).toBe(80);
      expect(useReviewStore.getState().feynman_result?.missed_points).toEqual([
        "作用域链",
      ]);
      expect(useReviewStore.getState().feynman_loading).toBe(false);
      expect(mockEvaluateFeynman).toHaveBeenCalledWith("sess1", "我的回答");
    });

    it("rolls back to question with an error on failure", async () => {
      seedReviewing();
      useReviewStore.setState({
        feynman_phase: "evaluating",
        feynman_question: {
          session_id: "sess1",
          question: "解释闭包",
          hint: null,
        },
      });
      mockEvaluateFeynman.mockRejectedValueOnce(new Error("评估失败"));

      await useReviewStore.getState().submitFeynmanAnswer("回答");

      expect(useReviewStore.getState().feynman_phase).toBe("question");
      expect(useReviewStore.getState().feynman_error).toBe("评估失败");
      expect(useReviewStore.getState().feynman_loading).toBe(false);
    });
  });

  describe("skipFeynman", () => {
    it("hides the overlay and clears question + error", () => {
      seedReviewing();
      useReviewStore.setState({
        feynman_phase: "question",
        feynman_question: {
          session_id: "sess1",
          question: "q",
          hint: null,
        },
        feynman_error: "err",
      });

      useReviewStore.getState().skipFeynman();

      expect(useReviewStore.getState().feynman_phase).toBe("hidden");
      expect(useReviewStore.getState().feynman_question).toBeNull();
      expect(useReviewStore.getState().feynman_error).toBeNull();
    });
  });

  describe("continueFromFeynman", () => {
    it("hides the overlay and clears result", () => {
      seedReviewing();
      useReviewStore.setState({
        feynman_phase: "feedback",
        feynman_result: {
          session_id: "sess1",
          accuracy: 70,
          completeness: 70,
          feedback: "ok",
          missed_points: [],
        },
      });

      useReviewStore.getState().continueFromFeynman();

      expect(useReviewStore.getState().feynman_phase).toBe("hidden");
      expect(useReviewStore.getState().feynman_result).toBeNull();
    });
  });
});
