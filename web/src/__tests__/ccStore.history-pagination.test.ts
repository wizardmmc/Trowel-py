import { describe, expect, it } from "vitest";

import { listHistory, mockCreate } from "./ccStoreTestHarness";
import { createAgentStore } from "../agent";

const row = (runtime: "claude_code" | "codex", id: string, time: number) => ({
  runtime,
  native_session_id: id,
  title: id,
  updated_at: time,
});

describe("createAgentStore - history pagination", () => {
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
    const store = createAgentStore();

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
    const store = createAgentStore();
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
    const store = createAgentStore();
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

  it("clears only the matching history problem after a successful retry", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    listHistory.mockRejectedValueOnce(new Error("history unavailable"));

    await store.getState().refreshHistory("/wd");

    expect(store.getState().sessions.s1.transportProblem?.operation).toBe(
      "history_list",
    );
    listHistory.mockResolvedValueOnce({ rows: [], nextCursor: null });

    await store.getState().refreshHistory("/wd");

    expect(store.getState().sessions.s1.transportError).toBeNull();
    expect(store.getState().sessions.s1.transportProblem).toBeNull();
  });

  it("preserves a newer problem while an older operation retry completes", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    listHistory.mockRejectedValueOnce(new Error("history unavailable"));
    await store.getState().refreshHistory("/wd");
    let resolveRetry!: (value: { rows: []; nextCursor: null }) => void;
    listHistory.mockImplementationOnce(
      () => new Promise((resolve) => { resolveRetry = resolve; }),
    );

    const retry = store.getState().refreshHistory("/wd");
    store.setState((state) => ({
      ...state,
      sessions: {
        ...state.sessions,
        s1: {
          ...state.sessions.s1,
          transportError: "newer failure",
          transportProblem: {
            code: "http_error",
            message: "newer failure",
            operation: "approval_answer",
            budgetMs: null,
            status: null,
            occurredAt: "2026-08-05T00:00:01.000Z",
          },
        },
      },
    }));
    resolveRetry({ rows: [], nextCursor: null });
    await retry;

    expect(store.getState().sessions.s1.transportProblem?.operation).toBe(
      "approval_answer",
    );
  });
});
