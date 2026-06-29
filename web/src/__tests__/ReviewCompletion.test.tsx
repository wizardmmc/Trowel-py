import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReviewCompletion } from "../components/review/ReviewCompletion";
import type { SessionStats } from "../api/client";

describe("ReviewCompletion", () => {
  const mockStats: SessionStats = {
    total: 5,
    avg_rating: 3.2,
    accuracy: 80.0,
  };

  it("renders session statistics", () => {
    render(
      <ReviewCompletion
        stats={mockStats}
        onBackToGarden={vi.fn()}
      />,
    );

    expect(screen.getByTestId("completion-title")).toHaveTextContent(
      "复习完成",
    );
    expect(screen.getByTestId("stat-total")).toHaveTextContent("5");
    expect(screen.getByTestId("stat-accuracy")).toHaveTextContent("80.0%");
    expect(screen.getByTestId("stat-avg-rating")).toHaveTextContent("3.2");
  });

  it("renders Back to Garden button", () => {
    render(
      <ReviewCompletion
        stats={mockStats}
        onBackToGarden={vi.fn()}
      />,
    );

    expect(screen.getByTestId("back-to-garden")).toBeInTheDocument();
  });

  it("calls onBackToGarden when button clicked", () => {
    const onBack = vi.fn();
    render(
      <ReviewCompletion stats={mockStats} onBackToGarden={onBack} />,
    );

    screen.getByTestId("back-to-garden").click();
    expect(onBack).toHaveBeenCalledOnce();
  });

  it("handles zero stats gracefully", () => {
    const zeroStats: SessionStats = { total: 0, avg_rating: 0.0, accuracy: 0.0 };
    render(
      <ReviewCompletion stats={zeroStats} onBackToGarden={vi.fn()} />,
    );

    expect(screen.getByTestId("stat-total")).toHaveTextContent("0");
    expect(screen.getByTestId("stat-accuracy")).toHaveTextContent("0.0%");
  });
});
