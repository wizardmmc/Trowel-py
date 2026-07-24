import { describe, expect, it } from "vitest";

import { listHistory } from "./ccStoreTestHarness";
import { createCcStore } from "../stores/ccStore";

const row = (runtime: "claude_code" | "codex", id: string, time: number) => ({
  runtime,
  native_session_id: id,
  title: id,
  updated_at: time,
});

describe("createCcStore - history pagination", () => {
  it("loads the first page, appends the next page and removes overlap", async () => {
    listHistory
      .mockResolvedValueOnce({
        rows: [row("claude_code", "cc-1", 3), row("codex", "cx-1", 2)],
        nextCursor: "page-2",
      })
      .mockResolvedValueOnce({
        rows: [row("codex", "cx-1", 2), row("claude_code", "cc-2", 1)],
        nextCursor: null,
      });
    const store = createCcStore();

    await store.getState().refreshHistory("/wd");
    await store.getState().loadMoreHistory();

    expect(store.getState().history.map((item) => item.native_session_id)).toEqual([
      "cc-1",
      "cx-1",
      "cc-2",
    ]);
    expect(store.getState().historyHasMore).toBe(false);
    expect(listHistory).toHaveBeenNthCalledWith(2, "/wd", {
      limit: 20,
      cursor: "page-2",
    });
  });

  it("coalesces concurrent load-more requests", async () => {
    let resolvePage!: (value: { rows: []; nextCursor: null }) => void;
    listHistory
      .mockResolvedValueOnce({ rows: [row("codex", "cx-1", 2)], nextCursor: "next" })
      .mockImplementationOnce(
        () => new Promise((resolve) => { resolvePage = resolve; }),
      );
    const store = createCcStore();
    await store.getState().refreshHistory("/wd");

    const first = store.getState().loadMoreHistory();
    const second = store.getState().loadMoreHistory();
    expect(listHistory).toHaveBeenCalledTimes(2);
    resolvePage({ rows: [], nextCursor: null });
    await Promise.all([first, second]);
  });

  it("clears rows immediately when the workdir changes", async () => {
    listHistory.mockResolvedValueOnce({
      rows: [row("claude_code", "cc-a", 1)],
      nextCursor: null,
    });
    const store = createCcStore();
    await store.getState().refreshHistory("/a");
    let resolveNext!: (value: { rows: []; nextCursor: null }) => void;
    listHistory.mockImplementationOnce(
      () => new Promise((resolve) => { resolveNext = resolve; }),
    );

    const refreshing = store.getState().refreshHistory("/b");
    expect(store.getState().history).toEqual([]);
    expect(store.getState().historyWorkdir).toBe("/b");
    resolveNext({ rows: [], nextCursor: null });
    await refreshing;
  });
});
