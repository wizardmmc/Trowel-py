import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ElicitationBlock } from "../components/cc/ElicitationBlock";
import type { ElicitationItem } from "../stores/ccStore";

const singlePending: ElicitationItem = {
  kind: "elicit",
  toolUseId: "call_1",
  requestId: "r1",
  status: "pending",
  resultText: null,
  answers: null,
  questions: [
    {
      question: "A or B?",
      header: "Pref",
      multiSelect: false,
      options: [
        { label: "A", description: "a desc" },
        { label: "B" },
      ],
    },
  ],
};

describe("ElicitationBlock", () => {
  it("renders the question title and numbered options when pending", () => {
    render(<ElicitationBlock item={singlePending} />);
    expect(screen.getByText("A or B?")).toBeTruthy();
    expect(screen.getByText("A")).toBeTruthy();
    expect(screen.getByText("B")).toBeTruthy();
    expect(screen.getByText("Other")).toBeTruthy();
  });

  it("single-select submits the chosen label (hideSubmitTab direct submit)", () => {
    const onAnswer = vi.fn();
    render(<ElicitationBlock item={singlePending} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByText("A"));
    fireEvent.click(screen.getByRole("button", { name: "Submit" }));
    expect(onAnswer).toHaveBeenCalledWith({ "A or B?": "A" });
  });

  it("Other option expands a text input and submits its text", () => {
    const onAnswer = vi.fn();
    render(<ElicitationBlock item={singlePending} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByText("Other"));
    const input = screen.getByPlaceholderText("Type a custom answer…");
    fireEvent.change(input, { target: { value: "custom X" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit" }));
    expect(onAnswer).toHaveBeenCalledWith({ "A or B?": "custom X" });
  });

  it("cancel invokes onCancel", () => {
    const onCancel = vi.fn();
    render(<ElicitationBlock item={singlePending} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("Chat about this invokes onCancel (decline + reply in natural language)", () => {
    const onCancel = vi.fn();
    render(<ElicitationBlock item={singlePending} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole("button", { name: /Chat about this/ }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("answered state echoes cc's result text", () => {
    const item: ElicitationItem = {
      ...singlePending,
      status: "answered",
      resultText: 'User has answered your questions: "A or B?"="A"',
    };
    render(<ElicitationBlock item={item} />);
    expect(screen.getByText(/User answered Claude's questions/)).toBeTruthy();
    expect(screen.getByText(/User has answered/)).toBeTruthy();
  });

  it("declined state shows the decline message", () => {
    const item: ElicitationItem = { ...singlePending, status: "declined" };
    render(<ElicitationBlock item={item} />);
    expect(screen.getByText(/declined to answer/)).toBeTruthy();
  });

  it("multi-question navigates Next -> SubmitView and submits all answers", () => {
    const onAnswer = vi.fn();
    const twoQ: ElicitationItem = {
      ...singlePending,
      questions: [
        {
          question: "Q1?",
          header: "Pref",
          multiSelect: false,
          options: [{ label: "A1" }],
        },
        {
          question: "Q2?",
          header: "Auth",
          multiSelect: false,
          options: [{ label: "A2" }],
        },
      ],
    };
    render(<ElicitationBlock item={twoQ} onAnswer={onAnswer} />);
    expect(screen.getByText(/Pre/)).toBeTruthy();
    expect(screen.getByText(/Aut/)).toBeTruthy();
    fireEvent.click(screen.getByText("A1"));
    fireEvent.click(screen.getByRole("button", { name: "Next →" }));
    fireEvent.click(screen.getByText("A2"));
    fireEvent.click(screen.getByRole("button", { name: "Next →" }));
    fireEvent.click(screen.getByRole("button", { name: "Submit answers" }));
    expect(onAnswer).toHaveBeenCalledWith({ "Q1?": "A1", "Q2?": "A2" });
  });
});
