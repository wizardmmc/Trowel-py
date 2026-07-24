import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ReviewCard } from "../components/review/ReviewCard";
import type { DueCard } from "../api/client";

function makeDueCard(overrides?: Partial<DueCard>): DueCard {
  return {
    card: {
      id: "test1",
      title: "What is Python?",
      category: "programming",
      explanation: "Python is a high-level programming language.",
      example: "print('hello')",
      difficulty: 3,
      source: null,
      tags: ["python", "basics"],
      status: "active",
      created_at: "2026-01-01T00:00:00",
      updated_at: "2026-01-01T00:00:00",
    },
    fsrs_state: {
      card_id: "test1",
      stability: 0.0,
      difficulty: 0.0,
      elapsed_days: 0,
      scheduled_days: 0,
      reps: 0,
      lapses: 0,
      state: 0,
      due: "2026-01-01T00:00:00",
      last_review: null,
    },
    plant_stage: "seed",
    ...overrides,
  };
}

describe("ReviewCard", () => {
  it("renders the card title on front side", () => {
    const dueCard = makeDueCard();
    render(
      <ReviewCard dueCard={dueCard} onRate={vi.fn()} disabled={false} />,
    );

    expect(screen.getByTestId("card-front")).toHaveTextContent("What is Python?");
  });

  it("does not show explanation initially (not flipped)", () => {
    const dueCard = makeDueCard();
    render(
      <ReviewCard dueCard={dueCard} onRate={vi.fn()} disabled={false} />,
    );

    expect(screen.queryByTestId("card-back")).toBeNull();
  });

  it("shows explanation after clicking flip button", () => {
    const dueCard = makeDueCard();
    render(
      <ReviewCard dueCard={dueCard} onRate={vi.fn()} disabled={false} />,
    );

    fireEvent.click(screen.getByTestId("flip-button"));
    expect(screen.getByTestId("card-back")).toHaveTextContent(
      "Python is a high-level programming language.",
    );
  });

  it("shows rating buttons only after flip", () => {
    const dueCard = makeDueCard();
    render(
      <ReviewCard dueCard={dueCard} onRate={vi.fn()} disabled={false} />,
    );

    expect(screen.queryByTestId("rate-again")).toBeNull();

    fireEvent.click(screen.getByTestId("flip-button"));
    expect(screen.getByTestId("rate-again")).toBeInTheDocument();
    expect(screen.getByTestId("rate-hard")).toBeInTheDocument();
    expect(screen.getByTestId("rate-good")).toBeInTheDocument();
    expect(screen.getByTestId("rate-easy")).toBeInTheDocument();
  });

  it("calls onRate with correct rating when button clicked", () => {
    const onRate = vi.fn();
    const dueCard = makeDueCard();
    render(<ReviewCard dueCard={dueCard} onRate={onRate} disabled={false} />);

    fireEvent.click(screen.getByTestId("flip-button"));
    fireEvent.click(screen.getByTestId("rate-good"));

    expect(onRate).toHaveBeenCalledWith(3);
  });

  it("disables buttons when disabled prop is true", () => {
    const dueCard = makeDueCard();
    render(
      <ReviewCard dueCard={dueCard} onRate={vi.fn()} disabled={true} />,
    );

    fireEvent.click(screen.getByTestId("flip-button"));
    expect(screen.getByTestId("rate-again")).toBeDisabled();
    expect(screen.getByTestId("rate-hard")).toBeDisabled();
    expect(screen.getByTestId("rate-good")).toBeDisabled();
    expect(screen.getByTestId("rate-easy")).toBeDisabled();
  });

  it("flips on Space key press", () => {
    const dueCard = makeDueCard();
    render(
      <ReviewCard dueCard={dueCard} onRate={vi.fn()} disabled={false} />,
    );

    fireEvent.keyDown(window, { code: "Space" });
    expect(screen.getByTestId("card-back")).toBeInTheDocument();
  });

  it("rates via keyboard shortcuts (1-4) when flipped", () => {
    const onRate = vi.fn();
    const dueCard = makeDueCard();
    render(<ReviewCard dueCard={dueCard} onRate={onRate} disabled={false} />);

    fireEvent.keyDown(window, { code: "Space" });

    fireEvent.keyDown(window, { key: "3" });
    expect(onRate).toHaveBeenCalledWith(3);
  });

  it("does not rate via keyboard when not flipped", () => {
    const onRate = vi.fn();
    const dueCard = makeDueCard();
    render(<ReviewCard dueCard={dueCard} onRate={onRate} disabled={false} />);

    fireEvent.keyDown(window, { key: "3" });
    expect(onRate).not.toHaveBeenCalled();
  });
});
