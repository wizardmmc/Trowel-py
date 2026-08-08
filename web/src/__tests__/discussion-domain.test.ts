/** 锁定研讨事件水位、快照版本和多人布局的不变量。 */

import { describe, expect, it } from "vitest";
import {
  INITIAL_DISCUSSION_DOMAIN_STATE,
  reduceDiscussion,
  type Discussion,
} from "../discussion/domain";
import { balancedRows } from "../discussion/ui/layout";

describe("discussion domain reducer", () => {
  it("drops duplicate SSE sequence numbers", () => {
    const first = reduceDiscussion(INITIAL_DISCUSSION_DOMAIN_STATE, {
      type: "event",
      event: event(8),
    });
    const duplicate = reduceDiscussion(first, {
      type: "event",
      event: event(8),
    });

    expect(first.lastSequence).toBe(8);
    expect(duplicate).toBe(first);
  });

  it("keeps the newer authoritative snapshot", () => {
    const current = reduceDiscussion(INITIAL_DISCUSSION_DOMAIN_STATE, {
      type: "snapshot",
      discussion: discussion(4),
    });
    const stale = reduceDiscussion(current, {
      type: "snapshot",
      discussion: discussion(3),
    });

    expect(stale).toBe(current);
    expect(stale.discussion?.version).toBe(4);
  });
});

describe("balanced discussion rows", () => {
  it.each([
    [2, [2]],
    [3, [3]],
    [4, [2, 2]],
    [5, [3, 2]],
    [6, [3, 3]],
    [7, [3, 2, 2]],
    [8, [3, 3, 2]],
  ])("lays out %i participants without an orphan", (count, expected) => {
    const rows = balancedRows(Array.from({ length: count }, (_, index) => index));
    expect(rows.map((row) => row.length)).toEqual(expected);
    expect(rows.flat()).toEqual(Array.from({ length: count }, (_, index) => index));
    expect(rows.at(-1)?.length).not.toBe(1);
  });
});

function event(sequence: number) {
  return {
    sequence,
    discussion_id: "discussion-1",
    type: "round_progressed",
    version: 3,
    round_number: 1,
  } as const;
}

function discussion(version: number): Discussion {
  return {
    id: "discussion-1",
    topic: "问题",
    workdir: "/repo",
    progression_mode: "user_guided",
    max_rounds: null,
    status: "waiting_user",
    version,
    active_round_number: 1,
    created_at: "2026-08-06T10:00:00Z",
    updated_at: "2026-08-06T10:00:00Z",
    completed_at: null,
    stopped_at: null,
    participants: [],
    messages: [],
    rounds: [],
    handoffs: [],
  };
}
