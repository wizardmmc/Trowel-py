import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionIdCopyButton } from "../components/cc/SessionIdCopyButton";

const writeText = vi.fn<(text: string) => Promise<void>>();

beforeEach(() => {
  vi.clearAllMocks();
  writeText.mockResolvedValue();
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
});

describe("SessionIdCopyButton", () => {
  it("copies the complete native session id and shows success feedback", async () => {
    const nativeSessionId = "019c1f22-96f2-7341-b85a-2f7244e63526";
    render(<SessionIdCopyButton sessionId={nativeSessionId} />);

    fireEvent.click(
      screen.getByRole("button", {
        name: `复制会话 ID ${nativeSessionId}`,
      }),
    );

    await waitFor(() => expect(writeText).toHaveBeenCalledWith(nativeSessionId));
    expect(
      screen.getByRole("button", {
        name: `已复制会话 ID ${nativeSessionId}`,
      }),
    ).toHaveAttribute("data-state", "copied");
    expect(screen.getByText(nativeSessionId)).toBeInTheDocument();
  });
});
