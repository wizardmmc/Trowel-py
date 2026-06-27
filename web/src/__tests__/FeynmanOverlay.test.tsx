import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { FeynmanOverlay } from "../components/review/FeynmanOverlay";
import type {
  FeynmanQuestion,
  FeynmanEvaluation,
} from "../api/client";

function makeQuestion(overrides: Partial<FeynmanQuestion> = {}): FeynmanQuestion {
  return {
    session_id: "sess1",
    question: "用你自己的话解释什么是闭包",
    hint: "想想函数和变量的关系",
    ...overrides,
  };
}

function makeEvaluation(
  overrides: Partial<FeynmanEvaluation> = {},
): FeynmanEvaluation {
  return {
    session_id: "sess1",
    accuracy: 80,
    completeness: 60,
    feedback: "基本到位，继续努力。",
    missed_points: ["作用域链", "变量生命周期"],
    ...overrides,
  };
}

const noop = vi.fn();

describe("FeynmanOverlay", () => {
  it("renders nothing when phase is hidden", () => {
    const { container } = render(
      <FeynmanOverlay
        phase="hidden"
        question={null}
        result={null}
        loading={false}
        error={null}
        onSkip={noop}
        onTryIt={noop}
        onSubmitAnswer={noop}
        onContinue={noop}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  describe("prompt phase", () => {
    it("shows the prompt text and Skip / Try It buttons", () => {
      render(
        <FeynmanOverlay
          phase="prompt"
          question={null}
          result={null}
          loading={false}
          error={null}
          onSkip={noop}
          onTryIt={noop}
          onSubmitAnswer={noop}
          onContinue={noop}
        />,
      );
      expect(screen.getByText(/想测试一下/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Skip" })).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Try It" }),
      ).toBeInTheDocument();
    });

    it("disables Try It and shows Loading text while loading", () => {
      render(
        <FeynmanOverlay
          phase="prompt"
          question={null}
          result={null}
          loading={true}
          error={null}
          onSkip={noop}
          onTryIt={noop}
          onSubmitAnswer={noop}
          onContinue={noop}
        />,
      );
      const tryIt = screen.getByRole("button", { name: /Loading/i });
      expect(tryIt).toBeDisabled();
    });

    it("shows the error message when set", () => {
      render(
        <FeynmanOverlay
          phase="prompt"
          question={null}
          result={null}
          loading={false}
          error="LLM 暂不可用"
          onSkip={noop}
          onTryIt={noop}
          onSubmitAnswer={noop}
          onContinue={noop}
        />,
      );
      expect(screen.getByText("LLM 暂不可用")).toBeInTheDocument();
    });

    it("calls onTryIt when Try It clicked", () => {
      const onTryIt = vi.fn();
      render(
        <FeynmanOverlay
          phase="prompt"
          question={null}
          result={null}
          loading={false}
          error={null}
          onSkip={noop}
          onTryIt={onTryIt}
          onSubmitAnswer={noop}
          onContinue={noop}
        />,
      );
      fireEvent.click(screen.getByRole("button", { name: "Try It" }));
      expect(onTryIt).toHaveBeenCalledTimes(1);
    });
  });

  describe("question phase", () => {
    it("shows the question, hint and a textarea", () => {
      render(
        <FeynmanOverlay
          phase="question"
          question={makeQuestion()}
          result={null}
          loading={false}
          error={null}
          onSkip={noop}
          onTryIt={noop}
          onSubmitAnswer={noop}
          onContinue={noop}
        />,
      );
      expect(screen.getByText(/闭包/)).toBeInTheDocument();
      expect(screen.getByText(/Hint/i)).toBeInTheDocument();
      expect(screen.getByRole("textbox")).toBeInTheDocument();
    });

    it("disables Submit while the answer is empty", () => {
      render(
        <FeynmanOverlay
          phase="question"
          question={makeQuestion()}
          result={null}
          loading={false}
          error={null}
          onSkip={noop}
          onTryIt={noop}
          onSubmitAnswer={noop}
          onContinue={noop}
        />,
      );
      expect(screen.getByRole("button", { name: "Submit" })).toBeDisabled();
    });

    it("enables Submit after typing and submits the trimmed answer", () => {
      const onSubmitAnswer = vi.fn();
      render(
        <FeynmanOverlay
          phase="question"
          question={makeQuestion()}
          result={null}
          loading={false}
          error={null}
          onSkip={noop}
          onTryIt={noop}
          onSubmitAnswer={onSubmitAnswer}
          onContinue={noop}
        />,
      );
      const textarea = screen.getByRole("textbox");
      fireEvent.change(textarea, { target: { value: "  我的解释  " } });
      const submit = screen.getByRole("button", { name: "Submit" });
      expect(submit).not.toBeDisabled();
      fireEvent.click(submit);
      expect(onSubmitAnswer).toHaveBeenCalledWith("我的解释");
    });

    it("submits on Ctrl+Enter", () => {
      const onSubmitAnswer = vi.fn();
      render(
        <FeynmanOverlay
          phase="question"
          question={makeQuestion()}
          result={null}
          loading={false}
          error={null}
          onSkip={noop}
          onTryIt={noop}
          onSubmitAnswer={onSubmitAnswer}
          onContinue={noop}
        />,
      );
      const textarea = screen.getByRole("textbox");
      fireEvent.change(textarea, { target: { value: "答案" } });
      fireEvent.keyDown(textarea, { key: "Enter", ctrlKey: true });
      expect(onSubmitAnswer).toHaveBeenCalledWith("答案");
    });

    it("omits the hint block when hint is null", () => {
      render(
        <FeynmanOverlay
          phase="question"
          question={makeQuestion({ hint: null })}
          result={null}
          loading={false}
          error={null}
          onSkip={noop}
          onTryIt={noop}
          onSubmitAnswer={noop}
          onContinue={noop}
        />,
      );
      expect(screen.queryByText(/Hint/i)).not.toBeInTheDocument();
    });
  });

  describe("evaluating phase", () => {
    it("shows the evaluating message", () => {
      render(
        <FeynmanOverlay
          phase="evaluating"
          question={null}
          result={null}
          loading={false}
          error={null}
          onSkip={noop}
          onTryIt={noop}
          onSubmitAnswer={noop}
          onContinue={noop}
        />,
      );
      expect(screen.getByText(/评估中/)).toBeInTheDocument();
    });
  });

  describe("feedback phase", () => {
    it("shows accuracy, completeness, feedback and missed points", () => {
      render(
        <FeynmanOverlay
          phase="feedback"
          question={null}
          result={makeEvaluation()}
          loading={false}
          error={null}
          onSkip={noop}
          onTryIt={noop}
          onSubmitAnswer={noop}
          onContinue={noop}
        />,
      );
      expect(screen.getByText("Accuracy")).toBeInTheDocument();
      expect(screen.getByText("Completeness")).toBeInTheDocument();
      expect(screen.getByText("80")).toBeInTheDocument();
      expect(screen.getByText("60")).toBeInTheDocument();
      expect(screen.getByText(/作用域链/)).toBeInTheDocument();
      expect(screen.getByText("Continue")).toBeInTheDocument();
    });

    it("omits the missed points block when empty", () => {
      render(
        <FeynmanOverlay
          phase="feedback"
          question={null}
          result={makeEvaluation({ missed_points: [] })}
          loading={false}
          error={null}
          onSkip={noop}
          onTryIt={noop}
          onSubmitAnswer={noop}
          onContinue={noop}
        />,
      );
      expect(screen.queryByText(/遗漏的知识点/)).not.toBeInTheDocument();
    });

    it("calls onContinue when Continue clicked", () => {
      const onContinue = vi.fn();
      render(
        <FeynmanOverlay
          phase="feedback"
          question={null}
          result={makeEvaluation()}
          loading={false}
          error={null}
          onSkip={noop}
          onTryIt={noop}
          onSubmitAnswer={noop}
          onContinue={onContinue}
        />,
      );
      fireEvent.click(screen.getByRole("button", { name: "Continue" }));
      expect(onContinue).toHaveBeenCalledTimes(1);
    });
  });

  it("calls onSkip from both prompt and question phases", () => {
      const onSkip = vi.fn();
      const { rerender } = render(
        <FeynmanOverlay
          phase="prompt"
          question={null}
          result={null}
          loading={false}
          error={null}
          onSkip={onSkip}
          onTryIt={noop}
          onSubmitAnswer={noop}
          onContinue={noop}
        />,
      );
      fireEvent.click(screen.getByRole("button", { name: "Skip" }));
      expect(onSkip).toHaveBeenCalledTimes(1);

      rerender(
        <FeynmanOverlay
          phase="question"
          question={makeQuestion()}
          result={null}
          loading={false}
          error={null}
          onSkip={onSkip}
          onTryIt={noop}
          onSubmitAnswer={noop}
          onContinue={noop}
        />,
      );
      fireEvent.click(screen.getByRole("button", { name: "Skip" }));
      expect(onSkip).toHaveBeenCalledTimes(2);
  });
});
