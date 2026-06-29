import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReviewModal } from "../components/cards/ReviewModal";
import type { CardDraft } from "../api/client";

const mockDraft: CardDraft = {
  id: "abc123",
  title: "Closure",
  category: "concept",
  explanation: "A closure is a function that captures variables from its enclosing scope.",
  example: "def outer(x): return lambda y: x + y",
  difficulty: 3,
  tags: ["python", "functions"],
  confidence: 4,
  source_type: "chat",
  source: null,
};

const defaultProps = {
  draft: mockDraft,
  currentIndex: 0,
  totalCount: 1,
  onAccept: vi.fn(),
  onReject: vi.fn(),
  onEdit: vi.fn(),
  onNext: vi.fn(),
  onPrev: vi.fn(),
  onClose: vi.fn(),
  loading: false,
};

describe("ReviewModal", () => {
  it("renders card title and category", () => {
    render(<ReviewModal {...defaultProps} />);

    expect(screen.getByText("Closure")).toBeInTheDocument();
    expect(screen.getByText("concept")).toBeInTheDocument();
  });

  it("renders accept and reject buttons", () => {
    render(<ReviewModal {...defaultProps} />);

    expect(screen.getByTestId("accept-button")).toBeInTheDocument();
    expect(screen.getByTestId("reject-button")).toBeInTheDocument();
  });

  it("shows card counter", () => {
    render(<ReviewModal {...defaultProps} totalCount={3} currentIndex={1} />);

    expect(screen.getByText("审核卡片（2/3）")).toBeInTheDocument();
  });

  it("calls onAccept when accept clicked", async () => {
    const onAccept = vi.fn();
    render(<ReviewModal {...defaultProps} onAccept={onAccept} />);

    await userEvent.click(screen.getByTestId("accept-button"));

    expect(onAccept).toHaveBeenCalledOnce();
  });

  it("calls onReject when reject clicked", async () => {
    const onReject = vi.fn();
    render(<ReviewModal {...defaultProps} onReject={onReject} />);

    await userEvent.click(screen.getByTestId("reject-button"));

    expect(onReject).toHaveBeenCalledOnce();
  });

  it("calls onClose when close button clicked", async () => {
    const onClose = vi.fn();
    render(<ReviewModal {...defaultProps} onClose={onClose} />);

    await userEvent.click(screen.getByTestId("close-modal"));

    expect(onClose).toHaveBeenCalledOnce();
  });

  it("shows nav buttons when multiple cards", () => {
    render(<ReviewModal {...defaultProps} totalCount={3} currentIndex={1} />);

    expect(screen.getByTestId("prev-button")).toBeInTheDocument();
    expect(screen.getByTestId("next-button")).toBeInTheDocument();
  });

  it("disables prev at first card", () => {
    render(<ReviewModal {...defaultProps} totalCount={3} currentIndex={0} />);

    expect(screen.getByTestId("prev-button")).toBeDisabled();
  });

  it("disables next at last card", () => {
    render(<ReviewModal {...defaultProps} totalCount={3} currentIndex={2} />);

    expect(screen.getByTestId("next-button")).toBeDisabled();
  });

  it("returns null when draft is null", () => {
    const { container } = render(
      <ReviewModal {...defaultProps} draft={null} />
    );

    expect(container.innerHTML).toBe("");
  });
});
