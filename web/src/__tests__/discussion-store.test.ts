/** 验证研讨切换不会清空可见快照，也不会让过期请求覆盖新选择。 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { INITIAL_REDUCER_STATE } from "../agent/domain";
import type {
  Discussion,
  DiscussionAttemptTimeline,
  DiscussionEvent,
} from "../discussion/domain";

const transport = vi.hoisted(() => ({
  getDiscussion: vi.fn(),
  getDiscussionAttemptEvents: vi.fn(),
  watchDiscussionEvents: vi.fn(),
}));

vi.mock("../discussion/transport", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../discussion/transport")>()),
  getDiscussion: transport.getDiscussion,
  getDiscussionAttemptEvents: transport.getDiscussionAttemptEvents,
  watchDiscussionEvents: transport.watchDiscussionEvents,
}));

import { INITIAL_DISCUSSION_DOMAIN_STATE } from "../discussion/domain";
import { useDiscussionStore } from "../discussion/application/store";

describe("discussion store open", () => {
  beforeEach(() => {
    transport.getDiscussion.mockReset();
    transport.getDiscussionAttemptEvents.mockReset();
    transport.watchDiscussionEvents.mockReset();
    transport.watchDiscussionEvents.mockImplementation(
      (_id, _sequence, _onEvent, signal: AbortSignal) =>
        new Promise<void>((resolve) => {
          signal.addEventListener("abort", () => resolve(), { once: true });
        }),
    );
    useDiscussionStore.setState({
      ...INITIAL_DISCUSSION_DOMAIN_STATE,
      discussions: [],
      loading: false,
      commandPending: null,
      error: null,
      attemptTimelines: {},
    });
  });

  it("keeps the current snapshot and live timeline visible while another discussion loads", async () => {
    const current = discussion("current");
    const timeline = attemptTimeline("attempt-current");
    useDiscussionStore.setState({
      discussion: current,
      discussions: [current],
      attemptTimelines: { [timeline.attemptId]: timeline },
    });
    const nextRequest = deferred<Discussion>();
    transport.getDiscussion.mockReturnValueOnce(nextRequest.promise);

    const opening = useDiscussionStore.getState().open("next");

    expect(useDiscussionStore.getState().discussion?.id).toBe("current");
    expect(useDiscussionStore.getState().attemptTimelines).toHaveProperty(
      "attempt-current",
    );
    expect(useDiscussionStore.getState().loading).toBe(true);

    nextRequest.resolve(discussion("next"));
    await opening;
    expect(useDiscussionStore.getState().discussion?.id).toBe("next");
    expect(useDiscussionStore.getState().attemptTimelines).toHaveProperty(
      "attempt-current",
    );
  });

  it("ignores an older open request that resolves after the latest selection", async () => {
    const first = deferred<Discussion>();
    const second = deferred<Discussion>();
    transport.getDiscussion
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);

    const openingFirst = useDiscussionStore.getState().open("first");
    const openingSecond = useDiscussionStore.getState().open("second");
    second.resolve(discussion("second"));
    await openingSecond;
    first.resolve(discussion("first"));
    await openingFirst;

    expect(useDiscussionStore.getState().discussion?.id).toBe("second");
  });

  it("repeats an in-flight snapshot refresh when another durable event arrives", async () => {
    let emit!: (event: DiscussionEvent) => void;
    transport.watchDiscussionEvents.mockImplementation(
      (_id, _sequence, onEvent, signal: AbortSignal) => {
        emit = onEvent;
        return new Promise<void>((resolve) => {
          signal.addEventListener("abort", () => resolve(), { once: true });
        });
      },
    );
    transport.getDiscussion.mockResolvedValueOnce(discussion("current"));
    await useDiscussionStore.getState().open("current");

    const firstRefresh = deferred<Discussion>();
    transport.getDiscussion
      .mockReturnValueOnce(firstRefresh.promise)
      .mockResolvedValueOnce({ ...discussion("current"), topic: "最新快照" });
    emit(durableEvent(1));
    await vi.waitFor(() => {
      expect(transport.getDiscussion).toHaveBeenCalledTimes(2);
    });
    emit(durableEvent(2));
    firstRefresh.resolve({ ...discussion("current"), topic: "中间快照" });

    await vi.waitFor(() => {
      expect(transport.getDiscussion).toHaveBeenCalledTimes(3);
      expect(useDiscussionStore.getState().discussion?.topic).toBe("最新快照");
    });
  });
});

function deferred<T>(): {
  readonly promise: Promise<T>;
  readonly resolve: (value: T) => void;
} {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function discussion(id: string): Discussion {
  return {
    id,
    topic: id,
    workdir: "/tmp/project",
    progression_mode: "user_guided",
    max_rounds: null,
    status: "draft",
    version: 1,
    active_round_number: null,
    created_at: "2026-08-08T00:00:00Z",
    updated_at: "2026-08-08T00:00:00Z",
    completed_at: null,
    stopped_at: null,
    participants: [],
    messages: [],
    rounds: [],
    handoffs: [],
  };
}

/** 构造不携带封闭正文、只推进公开快照水位的持久事件。 */
function durableEvent(sequence: number): DiscussionEvent {
  return {
    sequence,
    discussion_id: "current",
    type: "participant_completed",
    version: 1,
    round_number: 1,
  };
}

function attemptTimeline(attemptId: string): DiscussionAttemptTimeline {
  return {
    attemptId,
    participantId: "participant-1",
    roundNumber: 1,
    runtime: "claude_code",
    reducer: {
      ...INITIAL_REDUCER_STATE,
      phase: "thinking",
      meta: { ...INITIAL_REDUCER_STATE.meta, thinkingStartedAt: 1 },
    },
    lastSeq: 1,
    terminalStatus: null,
    needsReplay: false,
    availability: "live",
  };
}
