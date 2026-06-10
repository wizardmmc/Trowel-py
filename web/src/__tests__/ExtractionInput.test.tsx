import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ExtractionInput } from "../components/cards/ExtractionInput";

describe("ExtractionInput", () => {
  it("renders textarea and extract button", () => {
    render(<ExtractionInput onExtract={vi.fn()} loading={false} />);

    expect(screen.getByTestId("extraction-textarea")).toBeInTheDocument();
    expect(screen.getByTestId("extract-button")).toBeInTheDocument();
  });

  it("disables button when content is empty", () => {
    render(<ExtractionInput onExtract={vi.fn()} loading={false} />);

    expect(screen.getByTestId("extract-button")).toBeDisabled();
  });

  it("enables button when content is entered", async () => {
    render(<ExtractionInput onExtract={vi.fn()} loading={false} />);

    const textarea = screen.getByTestId("extraction-textarea");
    await userEvent.type(textarea, "some content");

    expect(screen.getByTestId("extract-button")).toBeEnabled();
  });

  it("calls onExtract with content when button clicked", async () => {
    const onExtract = vi.fn().mockResolvedValue(undefined);
    render(<ExtractionInput onExtract={onExtract} loading={false} />);

    await userEvent.type(screen.getByTestId("extraction-textarea"), "test diff");
    await userEvent.click(screen.getByTestId("extract-button"));

    expect(onExtract).toHaveBeenCalledWith("test diff");
  });

  it("shows loading state", () => {
    render(<ExtractionInput onExtract={vi.fn()} loading={true} />);

    expect(screen.getByText("Extracting...")).toBeInTheDocument();
  });

  it("detects git diff format", async () => {
    render(<ExtractionInput onExtract={vi.fn()} loading={false} />);

    await userEvent.type(
      screen.getByTestId("extraction-textarea"),
      "diff --git a/file.ts b/file.ts"
    );

    expect(screen.getByText(/git diff/)).toBeInTheDocument();
  });
});
