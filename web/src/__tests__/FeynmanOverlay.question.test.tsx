import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  makeQuestion,
  renderFeynman,
} from "./feynmanOverlayTestFixtures";

describe("FeynmanOverlay question phase", () => {
  it("shows the question, hint and a textarea", () => {
    renderFeynman({ phase: "question", question: makeQuestion() });
    expect(screen.getByText(/闭包/)).toBeInTheDocument();
    expect(screen.getByText(/提示/)).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });

  it("disables Submit while the answer is empty", () => {
    renderFeynman({ phase: "question", question: makeQuestion() });
    expect(screen.getByRole("button", { name: "提交" })).toBeDisabled();
  });

  it("enables Submit after typing and submits the trimmed answer", () => {
    const onSubmitAnswer = vi.fn();
    renderFeynman({
      phase: "question",
      question: makeQuestion(),
      onSubmitAnswer,
    });
    const textarea = screen.getByRole("textbox");
    fireEvent.change(textarea, { target: { value: "  我的解释  " } });
    const submit = screen.getByRole("button", { name: "提交" });
    expect(submit).not.toBeDisabled();
    fireEvent.click(submit);
    expect(onSubmitAnswer).toHaveBeenCalledWith("我的解释");
  });

  it("submits on Ctrl+Enter", () => {
    const onSubmitAnswer = vi.fn();
    renderFeynman({
      phase: "question",
      question: makeQuestion(),
      onSubmitAnswer,
    });
    const textarea = screen.getByRole("textbox");
    fireEvent.change(textarea, { target: { value: "答案" } });
    fireEvent.keyDown(textarea, { key: "Enter", ctrlKey: true });
    expect(onSubmitAnswer).toHaveBeenCalledWith("答案");
  });

  it("omits the hint block when hint is null", () => {
    renderFeynman({
      phase: "question",
      question: makeQuestion({ hint: null }),
    });
    expect(screen.queryByText(/Hint/i)).not.toBeInTheDocument();
  });
});
