import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  makeEvaluation,
  renderFeynman,
} from "./feynmanOverlayTestFixtures";

describe("FeynmanOverlay feedback phase", () => {
  it("shows accuracy, completeness, feedback and missed points", () => {
    renderFeynman({ phase: "feedback", result: makeEvaluation() });
    expect(screen.getByText("准确度")).toBeInTheDocument();
    expect(screen.getByText("完整度")).toBeInTheDocument();
    expect(screen.getByText("80")).toBeInTheDocument();
    expect(screen.getByText("60")).toBeInTheDocument();
    expect(screen.getByText(/作用域链/)).toBeInTheDocument();
    expect(screen.getByText("继续")).toBeInTheDocument();
  });

  it("omits the missed points block when empty", () => {
    renderFeynman({
      phase: "feedback",
      result: makeEvaluation({ missed_points: [] }),
    });
    expect(screen.queryByText(/遗漏的知识点/)).not.toBeInTheDocument();
  });

  it("calls onContinue when Continue clicked", () => {
    const onContinue = vi.fn();
    renderFeynman({
      phase: "feedback",
      result: makeEvaluation(),
      onContinue,
    });
    fireEvent.click(screen.getByRole("button", { name: "继续" }));
    expect(onContinue).toHaveBeenCalledTimes(1);
  });
});
