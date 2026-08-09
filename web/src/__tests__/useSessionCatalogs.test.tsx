/** 验证会话目录加载在首次请求和显式重试时保持一致的状态语义。 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { listModels, listSlashItems, type SlashItem } from "../api/cc";
import {
  listAgentConnectionOptions,
  listAgentModels,
  listAgentRuntimes,
} from "../agent/transport";
import { useSessionCatalogs } from "../components/cc/useSessionCatalogs";

vi.mock("../api/cc", () => ({
  listModels: vi.fn(),
  listSlashItems: vi.fn(),
}));

vi.mock("../agent/transport", () => ({
  listAgentConnectionOptions: vi.fn(),
  listAgentModels: vi.fn(),
  listAgentRuntimes: vi.fn(),
}));

const slashItem: SlashItem = {
  name: "review",
  description: "审查当前改动",
  source: "user",
  type: "command",
};

describe("useSessionCatalogs", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listModels).mockResolvedValue([]);
    vi.mocked(listAgentModels).mockResolvedValue([]);
    vi.mocked(listAgentConnectionOptions).mockResolvedValue([]);
    vi.mocked(listAgentRuntimes).mockResolvedValue([]);
  });

  it("hides the stale slash roster and reports loading during retry", async () => {
    let finishRetry: ((items: readonly SlashItem[]) => void) | undefined;
    vi.mocked(listSlashItems)
      .mockResolvedValueOnce([slashItem])
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishRetry = resolve;
          }),
      );
    const { result } = renderHook(() =>
      useSessionCatalogs("/repo", "claude-session"),
    );
    await waitFor(() => expect(result.current.slashItems).toEqual([slashItem]));

    act(() => result.current.retrySlashItems());

    expect(result.current.slashLoading).toBe(true);
    expect(result.current.slashItems).toEqual([]);
    act(() => finishRetry?.([]));
    await waitFor(() => expect(result.current.slashLoading).toBe(false));
  });
});
