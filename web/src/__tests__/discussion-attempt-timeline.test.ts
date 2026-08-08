/** 验证研讨 participant 复用 Agent reducer 且每个 attempt 互不串流。 */

import { describe, expect, it } from "vitest";
import {
  applyAttemptEvent,
  attemptTerminalStatus,
  attemptTimelineItems,
  createLiveAttemptTimeline,
  replayAttemptTimeline,
  splitCompletedItems,
} from "../discussion/domain";
import type { AgentEvent } from "../agent/transport/agentEvent";

describe("discussion attempt timeline", () => {
  it("creates an isolated turn when live stream has no user echo", () => {
    let timeline = createLiveAttemptTimeline("a1", "p1", 1, "claude_code");
    timeline = applyAttemptEvent(timeline, event(10, "turn_start", {}), 1);
    timeline = applyAttemptEvent(timeline, event(11, "thinking", { text: "检查" }), 2);
    timeline = applyAttemptEvent(timeline, event(12, "text", { text: "结论" }), 3);

    expect(attemptTimelineItems(timeline).map((item) => item.kind)).toEqual([
      "thinking",
      "text",
    ]);
    expect(timeline.needsReplay).toBe(false);
  });

  it("marks a sequence hole for native-history replay", () => {
    let timeline = createLiveAttemptTimeline("a1", "p1", 1, "claude_code");
    timeline = applyAttemptEvent(timeline, event(10, "turn_start", {}), 1);
    timeline = applyAttemptEvent(timeline, event(12, "text", { text: "缺片后文字" }), 3);

    expect(timeline.needsReplay).toBe(true);
  });

  it("does not mistake child-turn session sequence gaps for dropped root events", () => {
    let timeline = createLiveAttemptTimeline("a1", "p1", 1, "claude_code");
    timeline = applyAttemptEvent(timeline, event(10, "turn_start", {}), 1);
    timeline = applyAttemptEvent(timeline, event(14, "text", { text: "子事件后的根文字" }), 2);

    expect(timeline.needsReplay).toBe(false);
  });

  it("moves only trailing text out of the Worked fold", () => {
    const split = splitCompletedItems([
      { kind: "text", text: "中间解释" },
      {
        kind: "tool",
        toolUseId: "tool-1",
        toolName: "Read",
        input: {},
        status: "done",
        elapsedSeconds: 1,
        result: "ok",
        childTools: [],
      },
      { kind: "text", text: "最终结论" },
    ]);

    expect(split.workItems.map((item) => item.kind)).toEqual(["text", "tool"]);
    expect(split.trailingText).toBe("最终结论");
  });

  it("uses the durable attempt status when CC history has no terminal event", () => {
    const base = createLiveAttemptTimeline("a1", "p1", 1, "claude_code");
    const events = [
      event(10, "turn_start", {}),
      event(11, "text", { text: "失败前的部分回答" }),
    ];

    const failed = replayAttemptTimeline(base, events, "failed");
    const succeeded = replayAttemptTimeline(base, events, "succeeded");

    expect(attemptTerminalStatus(failed)).toBe("failed");
    expect(attemptTerminalStatus(succeeded)).toBe("succeeded");
  });

  it("normalizes a live null thinking duration instead of rendering null seconds", () => {
    let timeline = createLiveAttemptTimeline("a1", "p1", 1, "claude_code");
    timeline = applyAttemptEvent(timeline, event(10, "turn_start", {}), 1);
    timeline = applyAttemptEvent(
      timeline,
      event(11, "thinking", {
        text: "检查",
        thinking_duration_seconds: null,
      }),
      2,
    );

    expect(attemptTimelineItems(timeline)[0]).toEqual({
      kind: "thinking",
      text: "检查",
      thinkingDurationSeconds: undefined,
    });
  });
});

function event(
  seq: number,
  type: string,
  payload: Record<string, unknown>,
): AgentEvent {
  return {
    schema: "agent-event-v1",
    session_id: "session-1",
    runtime: "claude_code",
    seq,
    type,
    thread_id: null,
    turn_id: "turn-1",
    item_id: null,
    payload,
  };
}
