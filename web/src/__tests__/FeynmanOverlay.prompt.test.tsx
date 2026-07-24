import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { FeynmanOverlay } from "../components/review/FeynmanOverlay";
import {
  feynmanProps,
  makeQuestion,
  renderFeynman,
} from "./feynmanOverlayTestFixtures";

describe("FeynmanOverlay", () => {
  it("renders nothing when phase is hidden", () => {
    const { container } = renderFeynman();
    expect(container).toBeEmptyDOMElement();
  });

  describe("prompt phase", () => {
    it("shows the prompt text and Skip / Try It buttons", () => {
      renderFeynman({ phase: "prompt" });
      expect(screen.getByText(/想测试一下/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "跳过" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "试一下" })).toBeInTheDocument();
    });

    it("disables Try It and shows Loading text while loading", () => {
      renderFeynman({ phase: "prompt", loading: true });
      expect(screen.getByRole("button", { name: /加载中/ })).toBeDisabled();
    });

    it("shows the error message when set", () => {
      renderFeynman({ phase: "prompt", error: "LLM 暂不可用" });
      expect(screen.getByText("LLM 暂不可用")).toBeInTheDocument();
    });

    it("calls onTryIt when Try It clicked", () => {
      const onTryIt = vi.fn();
      renderFeynman({ phase: "prompt", onTryIt });
      fireEvent.click(screen.getByRole("button", { name: "试一下" }));
      expect(onTryIt).toHaveBeenCalledTimes(1);
    });
  });

  describe("evaluating phase", () => {
    it("shows the evaluating message", () => {
      renderFeynman({ phase: "evaluating" });
      expect(screen.getByText(/评估中/)).toBeInTheDocument();
    });
  });

  it("calls onSkip from both prompt and question phases", () => {
    const onSkip = vi.fn();
    const { rerender } = renderFeynman({ phase: "prompt", onSkip });
    fireEvent.click(screen.getByRole("button", { name: "跳过" }));
    expect(onSkip).toHaveBeenCalledTimes(1);

    rerender(
      <FeynmanOverlay
        {...feynmanProps({
          phase: "question",
          question: makeQuestion(),
          onSkip,
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "跳过" }));
    expect(onSkip).toHaveBeenCalledTimes(2);
  });
});
