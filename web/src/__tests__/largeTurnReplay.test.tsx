import { act, render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MessageList } from "../agent/ui";
import { getExpectedRuntimePresentation } from "../agent/runtimes";
import {
  LARGE_TURN_MANIFEST,
  buildLargeTurnReplay,
  replayLargeTurnEvents,
} from "../perf/largeTurnReplay";

const CODEX_PRESENTATION = getExpectedRuntimePresentation("codex");

describe("large-turn replay fixture", () => {
  it("freezes the sanitized L01 load shape", () => {
    const replay = buildLargeTurnReplay();
    const counts = replay.events.reduce<Record<string, number>>(
      (all, event) => ({
        ...all,
        [event.type]: (all[event.type] ?? 0) + 1,
      }),
      {},
    );
    const diffs = replay.events
      .filter((event) => event.type === "turn_diff_updated")
      .map((event) => event.payload.diff)
      .filter((diff): diff is string => typeof diff === "string");
    const toolResults = replay.events
      .filter((event) => event.type === "tool_result")
      .map((event) => event.payload.content)
      .filter((content): content is string => typeof content === "string");

    expect(replay.events).toHaveLength(2_405);
    expect(counts).toMatchObject({
      text: 1_784,
      turn_diff_updated: 159,
      usage_updated: 156,
      tool_call: 128,
      tool_result: 128,
      status: 46,
      session_started: 1,
      user: 1,
      turn_start: 1,
      finished: 1,
    });
    expect(replay.events.map((event) => event.seq)).toEqual(
      Array.from({ length: 2_405 }, (_, index) => index + 1),
    );
    expect(diffs.reduce((total, diff) => total + diff.length, 0)).toBe(
      10_440_000,
    );
    expect(Math.max(...diffs.map((diff) => diff.length))).toBe(88_296);
    expect(
      toolResults.reduce((total, content) => total + content.length, 0),
    ).toBe(1_600_000);
    expect(LARGE_TURN_MANIFEST.expectedDom.diffLines).toBe(1_011);
    expect(replay.initialSession.capabilities).toEqual(
      CODEX_PRESENTATION.expectedCapabilities,
    );

    const serialized = JSON.stringify(replay);
    expect(serialized).not.toMatch(/\/Users\/|hamxf|sk-[A-Za-z0-9_-]{20,}/);
  });

  it("reaches the same reducer end state on every run", () => {
    const replay = buildLargeTurnReplay();
    const state = replayLargeTurnEvents(replay);
    const currentTurn = state.turns.at(-1);
    const tools =
      currentTurn?.items.filter((item) => item.kind === "tool") ?? [];
    const diffLines = tools.reduce(
      (total, tool) =>
        total +
        (tool.kind === "tool"
          ? (tool.writeDiff?.hunks.reduce(
              (hunkTotal, hunk) =>
                hunkTotal +
                hunk.lines.filter((line) => !line.startsWith("\\")).length,
              0,
            ) ?? 0)
          : 0),
      0,
    );

    expect(state.turns).toHaveLength(2);
    expect(currentTurn?.status).toBe("done");
    expect(tools).toHaveLength(128);
    expect(tools.every((tool) => tool.status === "done")).toBe(true);
    expect(diffLines).toBe(1_011);
    expect(state.phase).toBe("done");
    expect(state.lastSeq).toBe(2_405);
    expect(state.needsReplay).toBe(false);
    expect(state.turnDiff?.diff).toHaveLength(88_296);
    expect(state.meta.usage).toMatchObject({
      total: { totalTokens: 12_405 },
    });
  });

  it("keeps all tool summaries, bounds the initial diff DOM, and exposes every line on demand", () => {
    const replay = buildLargeTurnReplay();
    const state = replayLargeTurnEvents(replay);
    const { container } = render(
      <MessageList
        turns={state.turns}
        streaming={false}
        presentation={CODEX_PRESENTATION}
        workdir="/fixture/repo"
      />,
    );

    expect(container.querySelectorAll(".cc-turn")).toHaveLength(2);
    expect(container.querySelectorAll(".cc-tool")).toHaveLength(128);
    expect(container.querySelectorAll(".cc-tool__diff-line")).toHaveLength(
      32,
    );
    const collapsed = container.querySelectorAll<HTMLButtonElement>(
      '.cc-tool__summary[aria-expanded="false"]',
    );
    expect(collapsed).toHaveLength(124);
    act(() => {
      collapsed.forEach((button) => button.click());
    });
    expect(container.querySelectorAll(".cc-tool__diff-line")).toHaveLength(
      1_011,
    );
  });
});
