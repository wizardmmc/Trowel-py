import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReviewModal } from "../components/cards/ReviewModal";
import type { CardDraft } from "../api/client";
import { ORIGINAL_ID } from "../stores/cardStore";

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
  reExplainRegens: [],
  reExplainSelectedId: ORIGINAL_ID,
  reExplainLoading: false,
  reExplainError: null,
  onRegenerate: vi.fn(),
  onSelectCandidate: vi.fn(),
  onResetReExplain: vi.fn(),
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

describe("ReviewModal re-explain", () => {
  it("always shows V0 (the draft's explanation) as the first candidate and pre-selects it", () => {
    render(<ReviewModal {...defaultProps} />);
    const v0 = screen.getByTestId(`re-explain-cand-${ORIGINAL_ID}`);
    expect(v0).toHaveTextContent("原始版本");
    expect(v0).toHaveClass("re-explain__cand--selected");
  });

  it("renders regenerated candidates after V0", () => {
    render(
      <ReviewModal
        {...defaultProps}
        reExplainRegens={[
          { id: "regen-1", tag: "重写 1", text: "一个新角度的解释" },
        ]}
      />,
    );
    expect(screen.getByTestId("re-explain-cand-regen-1")).toHaveTextContent(
      "一个新角度的解释",
    );
  });

  it("marks only the selected candidate with --selected", () => {
    render(
      <ReviewModal
        {...defaultProps}
        reExplainRegens={[{ id: "regen-1", tag: "重写 1", text: "x" }]}
        reExplainSelectedId="regen-1"
      />,
    );
    expect(screen.getByTestId("re-explain-cand-regen-1")).toHaveClass(
      "re-explain__cand--selected",
    );
    expect(
      screen.getByTestId(`re-explain-cand-${ORIGINAL_ID}`),
    ).not.toHaveClass("re-explain__cand--selected");
  });

  it("calls onSelectCandidate when a candidate is clicked", () => {
    const onSelectCandidate = vi.fn();
    render(
      <ReviewModal
        {...defaultProps}
        onSelectCandidate={onSelectCandidate}
        reExplainRegens={[{ id: "regen-1", tag: "重写 1", text: "x" }]}
      />,
    );
    fireEvent.click(screen.getByTestId("re-explain-cand-regen-1"));
    expect(onSelectCandidate).toHaveBeenCalledWith("regen-1");
  });

  it("disables 再生成 once 2 regens exist (invariant 3 cap)", () => {
    render(
      <ReviewModal
        {...defaultProps}
        reExplainRegens={[
          { id: "regen-1", tag: "重写 1", text: "a" },
          { id: "regen-2", tag: "重写 2", text: "b" },
        ]}
      />,
    );
    expect(screen.getByTestId("re-explain-regen")).toBeDisabled();
  });

  it("enables 再生成 below the cap", () => {
    render(<ReviewModal {...defaultProps} />);
    expect(screen.getByTestId("re-explain-regen")).not.toBeDisabled();
  });

  it("calls onRegenerate with the trimmed hint when present", () => {
    const onRegenerate = vi.fn();
    render(<ReviewModal {...defaultProps} onRegenerate={onRegenerate} />);
    fireEvent.change(screen.getByTestId("re-explain-hint"), {
      target: { value: "  更通俗  " },
    });
    fireEvent.click(screen.getByTestId("re-explain-regen"));
    expect(onRegenerate).toHaveBeenCalledWith("更通俗");
  });

  it("calls onRegenerate with undefined when the hint is empty", () => {
    const onRegenerate = vi.fn();
    render(<ReviewModal {...defaultProps} onRegenerate={onRegenerate} />);
    fireEvent.click(screen.getByTestId("re-explain-regen"));
    expect(onRegenerate).toHaveBeenCalledWith(undefined);
  });

  it("calls onResetReExplain when 取消 is clicked", () => {
    const onResetReExplain = vi.fn();
    render(
      <ReviewModal {...defaultProps} onResetReExplain={onResetReExplain} />,
    );
    fireEvent.click(screen.getByTestId("re-explain-cancel"));
    expect(onResetReExplain).toHaveBeenCalled();
  });

  it("accept uses onAccept (no edit) when the original is selected", () => {
    const onAccept = vi.fn();
    const onEdit = vi.fn();
    render(
      <ReviewModal {...defaultProps} onAccept={onAccept} onEdit={onEdit} />,
    );
    fireEvent.click(screen.getByTestId("accept-button"));
    expect(onAccept).toHaveBeenCalled();
    expect(onEdit).not.toHaveBeenCalled();
  });

  it("accept writes the selected regen back via onEdit({ explanation })", () => {
    const onAccept = vi.fn();
    const onEdit = vi.fn();
    render(
      <ReviewModal
        {...defaultProps}
        onAccept={onAccept}
        onEdit={onEdit}
        reExplainRegens={[
          { id: "regen-1", tag: "重写 1", text: "更好的解释" },
        ]}
        reExplainSelectedId="regen-1"
      />,
    );
    fireEvent.click(screen.getByTestId("accept-button"));
    expect(onEdit).toHaveBeenCalledWith({ explanation: "更好的解释" });
    expect(onAccept).not.toHaveBeenCalled();
  });
});
