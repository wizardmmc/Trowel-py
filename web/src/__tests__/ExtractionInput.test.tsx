import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ExtractionInput } from "../components/cards/ExtractionInput";

/** Build a File, optionally overriding `.size` (to test the 10MB cap without
 *  actually allocating a huge string in memory). */
function makeFile(
  name: string,
  content: string,
  type = "text/plain",
  sizeOverride?: number,
): File {
  const file = new File([content], name, { type });
  if (sizeOverride !== undefined) {
    Object.defineProperty(file, "size", { value: sizeOverride, configurable: true });
  }
  return file;
}

describe("ExtractionInput", () => {
  it("renders textarea, extract button and file input", () => {
    render(
      <ExtractionInput
        onExtract={vi.fn()}
        onExtractConversation={vi.fn()}
        loading={false}
      />,
    );
    expect(screen.getByTestId("extraction-textarea")).toBeInTheDocument();
    expect(screen.getByTestId("extract-button")).toBeInTheDocument();
    expect(screen.getByTestId("file-input")).toBeInTheDocument();
  });

  it("disables extract button when content is empty", () => {
    render(
      <ExtractionInput
        onExtract={vi.fn()}
        onExtractConversation={vi.fn()}
        loading={false}
      />,
    );
    expect(screen.getByTestId("extract-button")).toBeDisabled();
  });

  it("enables extract button when content is entered", async () => {
    render(
      <ExtractionInput
        onExtract={vi.fn()}
        onExtractConversation={vi.fn()}
        loading={false}
      />,
    );
    await userEvent.type(screen.getByTestId("extraction-textarea"), "some content");
    expect(screen.getByTestId("extract-button")).toBeEnabled();
  });

  it("calls onExtract with content when button clicked", async () => {
    const onExtract = vi.fn().mockResolvedValue(undefined);
    render(
      <ExtractionInput
        onExtract={onExtract}
        onExtractConversation={vi.fn()}
        loading={false}
      />,
    );
    await userEvent.type(screen.getByTestId("extraction-textarea"), "test diff");
    await userEvent.click(screen.getByTestId("extract-button"));
    expect(onExtract).toHaveBeenCalledWith("test diff");
  });

  it("shows loading state", () => {
    render(
      <ExtractionInput
        onExtract={vi.fn()}
        onExtractConversation={vi.fn()}
        loading={true}
      />,
    );
    expect(screen.getByText("提取中…")).toBeInTheDocument();
  });

  it("detects git diff format badge", async () => {
    render(
      <ExtractionInput
        onExtract={vi.fn()}
        onExtractConversation={vi.fn()}
        loading={false}
      />,
    );
    await userEvent.type(
      screen.getByTestId("extraction-textarea"),
      "diff --git a/file.ts b/file.ts",
    );
    expect(screen.getByText(/git diff/i)).toBeInTheDocument();
  });

  it("uploads .jsonl and calls onExtractConversation with file text", async () => {
    const onExtractConversation = vi.fn().mockResolvedValue(undefined);
    render(
      <ExtractionInput
        onExtract={vi.fn()}
        onExtractConversation={onExtractConversation}
        loading={false}
      />,
    );
    const content = '{"type":"user","message":{"role":"user","content":"hello there"}}';
    await userEvent.upload(
      screen.getByTestId("file-input"),
      makeFile("chat.jsonl", content, "application/json"),
    );
    await waitFor(() => expect(onExtractConversation).toHaveBeenCalledTimes(1));
    expect(onExtractConversation.mock.calls[0][0]).toContain("hello there");
  });

  it("uploads .txt and fills textarea without calling onExtractConversation", async () => {
    const onExtractConversation = vi.fn();
    render(
      <ExtractionInput
        onExtract={vi.fn()}
        onExtractConversation={onExtractConversation}
        loading={false}
      />,
    );
    await userEvent.upload(
      screen.getByTestId("file-input"),
      makeFile("notes.txt", "plain text here"),
    );
    await waitFor(() =>
      expect(screen.getByTestId("extraction-textarea")).toHaveValue("plain text here"),
    );
    expect(onExtractConversation).not.toHaveBeenCalled();
  });

  it("rejects file over 10MB and shows error", async () => {
    const onExtractConversation = vi.fn();
    render(
      <ExtractionInput
        onExtract={vi.fn()}
        onExtractConversation={onExtractConversation}
        loading={false}
      />,
    );
    // override size to 11MB without allocating the bytes
    await userEvent.upload(
      screen.getByTestId("file-input"),
      makeFile("big.jsonl", "{}", "application/json", 11 * 1024 * 1024),
    );
    await waitFor(() =>
      expect(screen.getByText(/文件过大|10mb|上限/)).toBeInTheDocument(),
    );
    expect(onExtractConversation).not.toHaveBeenCalled();
  });

  it("shows JSONL format badge after uploading .jsonl", async () => {
    render(
      <ExtractionInput
        onExtract={vi.fn()}
        onExtractConversation={vi.fn().mockResolvedValue(undefined)}
        loading={false}
      />,
    );
    const content = '{"type":"user","message":{"role":"user","content":"hi"}}';
    await userEvent.upload(
      screen.getByTestId("file-input"),
      makeFile("chat.jsonl", content, "application/json"),
    );
    await waitFor(() =>
      expect(screen.getByText(/CC JSONL/i)).toBeInTheDocument(),
    );
  });

  it("drag-and-drops a .jsonl file onto the drop zone", async () => {
    const onExtractConversation = vi.fn().mockResolvedValue(undefined);
    render(
      <ExtractionInput
        onExtract={vi.fn()}
        onExtractConversation={onExtractConversation}
        loading={false}
      />,
    );
    const content = '{"type":"user","message":{"role":"user","content":"dropped"}}';
    fireEvent.drop(screen.getByTestId("drop-zone"), {
      dataTransfer: {
        files: [makeFile("dropped.jsonl", content, "application/json")],
      },
    });
    await waitFor(() => expect(onExtractConversation).toHaveBeenCalledTimes(1));
    expect(onExtractConversation.mock.calls[0][0]).toContain("dropped");
  });
});
