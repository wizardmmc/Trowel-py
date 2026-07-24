import { render } from "@testing-library/react";
import type { ComponentProps } from "react";
import type { FeynmanEvaluation, FeynmanQuestion } from "../api/client";
import { FeynmanOverlay } from "../components/review/FeynmanOverlay";

type FeynmanOverlayProps = ComponentProps<typeof FeynmanOverlay>;

export function makeQuestion(
  overrides: Partial<FeynmanQuestion> = {},
): FeynmanQuestion {
  return {
    session_id: "sess1",
    question: "用你自己的话解释什么是闭包",
    hint: "想想函数和变量的关系",
    ...overrides,
  };
}

export function makeEvaluation(
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

export function feynmanProps(
  overrides: Partial<FeynmanOverlayProps> = {},
): FeynmanOverlayProps {
  return {
    phase: "hidden",
    question: null,
    result: null,
    loading: false,
    error: null,
    onSkip: () => {},
    onTryIt: () => {},
    onSubmitAnswer: () => {},
    onContinue: () => {},
    ...overrides,
  };
}

export function renderFeynman(overrides: Partial<FeynmanOverlayProps> = {}) {
  return render(<FeynmanOverlay {...feynmanProps(overrides)} />);
}
