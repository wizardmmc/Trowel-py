import type { PerSessionState } from "../agent/application";
import {
  createNewSessionState,
  reduceAgentEvent,
} from "../agent/application";
import type { AgentEvent, AgentSession, DiffHunk } from "../agent/transport";

const SESSION_ID = "fixture-large-turn";
const THREAD_ID = "fixture-thread";
const TURN_ID = "fixture-turn";
const TEXT_EVENT_COUNT = 1_784;
const TURN_DIFF_EVENT_COUNT = 159;
const USAGE_EVENT_COUNT = 156;
const TOOL_PAIR_COUNT = 128;
const SUPPORT_EVENT_COUNT = 46;
const TEXT_CHAR_COUNT = 2_994;
const TOOL_RESULT_CHAR_COUNT = 1_600_000;
const TURN_DIFF_CHAR_COUNT = 10_440_000;
const TURN_DIFF_MAX_CHARS = 88_296;
const DIFF_LINE_COUNT = 1_011;

/**
 * L01 只记录了主要事件的精确分类。加上四个回放生命周期事件后，剩余
 * 46 个事件用无正文的 status 补齐，以复现 reducer 和订阅更新次数。
 */
export const LARGE_TURN_MANIFEST = {
  id: "slice-433-l01-large-turn",
  schema: "trowel-large-turn-replay-v1",
  provenance: {
    date: "2026-07-31",
    method: "sanitized structural reconstruction",
    verifiedEventTypes: [
      "text",
      "turn_diff_updated",
      "usage_updated",
      "tool_call",
      "tool_result",
    ],
  },
  events: {
    total: 2_405,
    text: TEXT_EVENT_COUNT,
    turnDiffUpdated: TURN_DIFF_EVENT_COUNT,
    usageUpdated: USAGE_EVENT_COUNT,
    toolCall: TOOL_PAIR_COUNT,
    toolResult: TOOL_PAIR_COUNT,
    lifecycle: 4,
    representativeSupport: SUPPORT_EVENT_COUNT,
  },
  payload: {
    textChars: TEXT_CHAR_COUNT,
    toolResultChars: TOOL_RESULT_CHAR_COUNT,
    turnDiffChars: TURN_DIFF_CHAR_COUNT,
    maxTurnDiffChars: TURN_DIFF_MAX_CHARS,
  },
  expectedDom: {
    visibleTurns: 2,
    toolBlocks: TOOL_PAIR_COUNT,
    diffLines: DIFF_LINE_COUNT,
  },
} as const;

export interface LargeTurnReplay {
  readonly initialSession: PerSessionState;
  readonly events: readonly AgentEvent[];
}

let cachedReplay: LargeTurnReplay | null = null;

/** 生成不含本机路径、正文和凭据的确定性大回合输入。 */
export function buildLargeTurnReplay(): LargeTurnReplay {
  if (cachedReplay !== null) return cachedReplay;

  const events: AgentEvent[] = [];
  let seq = 0;
  const add = (
    type: string,
    payload: Readonly<Record<string, unknown>>,
    itemId: string | null = null,
  ) => {
    seq += 1;
    events.push({
      schema: "agent-event-v1",
      session_id: SESSION_ID,
      runtime: "codex",
      seq,
      type,
      thread_id: THREAD_ID,
      turn_id: type === "session_started" ? null : TURN_ID,
      item_id: itemId,
      payload,
    });
  };

  add("session_started", {
    model: "fixture-model",
    cwd: "/fixture/repo",
    cc_session_id: THREAD_ID,
    tools: ["apply_patch"],
    permission_profile: "workspace-write",
    effective_sandbox: "workspace-write",
    effective_approval: "on-request",
    network_access: false,
  });
  add("user", { text: "运行去身份化的大回合回放。" });
  add("turn_start", { turn_id: TURN_ID, revertible: false });

  const auxiliary = buildAuxiliaryEvents();
  const textChunks = buildTextChunks();
  let auxiliaryIndex = 0;
  textChunks.forEach((text, textIndex) => {
    add("text", { text });
    const target = Math.floor(
      ((textIndex + 1) * auxiliary.length) / textChunks.length,
    );
    while (auxiliaryIndex < target) {
      const event = auxiliary[auxiliaryIndex];
      add(event.type, event.payload, event.itemId);
      auxiliaryIndex += 1;
    }
  });

  add("finished", {
    usage: { total_tokens: 12_405 },
    total_cost_usd: 0,
    num_turns: 1,
  });

  if (events.length !== LARGE_TURN_MANIFEST.events.total) {
    throw new Error(`large-turn fixture built ${events.length} events`);
  }

  cachedReplay = {
    initialSession: buildInitialSession(),
    events,
  };
  return cachedReplay;
}

/** 使用线上 live/history 共用的单会话 reducer 完整回放。 */
export function replayLargeTurnEvents(
  replay: LargeTurnReplay = buildLargeTurnReplay(),
): PerSessionState {
  let session = replay.initialSession;
  for (const event of replay.events) {
    const reduced = reduceAgentEvent(session, event);
    if (reduced.kind !== "updated") {
      throw new Error(
        `large-turn event ${event.seq} reduced as ${reduced.kind}`,
      );
    }
    session = reduced.session;
  }
  return session;
}

interface AuxiliaryEvent {
  readonly type: string;
  readonly payload: Readonly<Record<string, unknown>>;
  readonly itemId: string | null;
}

function buildAuxiliaryEvents(): readonly AuxiliaryEvent[] {
  const events: AuxiliaryEvent[] = [];
  const turnDiffLengths = distributeTurnDiffLengths();

  for (let index = 0; index < TURN_DIFF_EVENT_COUNT; index += 1) {
    events.push({
      type: "turn_diff_updated",
      payload: {
        turn_id: TURN_ID,
        diff: buildSizedTurnDiff(index, turnDiffLengths[index]),
      },
      itemId: null,
    });

    if (index < USAGE_EVENT_COUNT) {
      events.push({
        type: "usage_updated",
        payload: {
          total: {
            totalTokens: 10_855 + index * 10,
            inputTokens: 8_000 + index * 8,
            outputTokens: 2_000 + index * 2,
          },
          last: { totalTokens: 80 + (index % 17) },
          model_context_window: 258_400,
        },
        itemId: null,
      });
    }

    if (index < TOOL_PAIR_COUNT) {
      const toolUseId = `fixture-tool-${index + 1}`;
      events.push({
        type: "tool_call",
        payload: {
          tool_use_id: toolUseId,
          tool_name: "apply_patch",
          input: { paths: [`/fixture/repo/src/file-${index + 1}.ts`] },
        },
        itemId: toolUseId,
      });
      events.push({
        type: "tool_result",
        payload: {
          tool_use_id: toolUseId,
          content: buildSizedToolResult(index),
          write_diff: {
            type: "update",
            hunks: [buildRenderedDiffHunk(index)],
          },
          exit_code: 0,
          duration_ms: 20 + index,
          cwd: "/fixture/repo",
          status: "completed",
        },
        itemId: toolUseId,
      });
    }

    if (index < SUPPORT_EVENT_COUNT) {
      events.push({
        type: "status",
        payload: { stage: "running" },
        itemId: null,
      });
    }
  }

  return events;
}

function buildInitialSession(): PerSessionState {
  const session: AgentSession = {
    session_id: SESSION_ID,
    runtime: "codex",
    native_session_id: THREAD_ID,
    workdir: "/fixture/repo",
    model: "fixture-model",
    effort: "medium",
    permission: "Workspace write · on-request",
    memory_enabled: true,
    profile_enabled: true,
    capabilities: ["tools", "interrupt"],
    name: "fixture-repo",
    display_title: "Large turn replay",
    title_source: "manual",
    connected: true,
    running: true,
  };
  const fresh = createNewSessionState(session, {
    workdir: session.workdir,
    runtime: "codex",
    model: session.model ?? undefined,
    effort: session.effort ?? undefined,
  });
  return {
    ...fresh,
    turns: [
      {
        id: "fixture-history-turn",
        userText: "上一轮保留用于核对懒加载窗口。",
        items: [{ kind: "text", text: "上一轮已完成。" }],
        status: "done",
        turnId: "fixture-history-native-turn",
        revertible: false,
        durationSeconds: 1,
      },
    ],
  };
}

function buildTextChunks(): readonly string[] {
  const chunks = [
    "fixture-ok!",
    ...Array.from({ length: 1_200 }, (_, index) =>
      String.fromCharCode(97 + (index % 26)).repeat(2),
    ),
    ...Array.from({ length: 583 }, (_, index) =>
      String.fromCharCode(97 + (index % 26)),
    ),
  ];
  const chars = chunks.reduce((total, chunk) => total + chunk.length, 0);
  if (chunks.length !== TEXT_EVENT_COUNT || chars !== TEXT_CHAR_COUNT) {
    throw new Error("large-turn text distribution is invalid");
  }
  return chunks;
}

function distributeTurnDiffLengths(): readonly number[] {
  const regularTotal = TURN_DIFF_CHAR_COUNT - TURN_DIFF_MAX_CHARS;
  const regularCount = TURN_DIFF_EVENT_COUNT - 1;
  const base = Math.floor(regularTotal / regularCount);
  const remainder = regularTotal - base * regularCount;
  return [
    ...Array.from(
      { length: regularCount },
      (_, index) => base + (index < remainder ? 1 : 0),
    ),
    TURN_DIFF_MAX_CHARS,
  ];
}

function buildSizedTurnDiff(index: number, length: number): string {
  const prefix =
    `*** Begin Patch\n*** Update File: /fixture/repo/src/file-${index + 1}.ts\n` +
    "@@\n";
  const line = "+fixture line\n";
  const remaining = length - prefix.length;
  if (remaining < 0) throw new Error("turn diff target is too small");
  return prefix + line.repeat(Math.ceil(remaining / line.length)).slice(0, remaining);
}

function buildSizedToolResult(index: number): string {
  const length = TOOL_RESULT_CHAR_COUNT / TOOL_PAIR_COUNT;
  const prefix = `Applied fixture change ${index + 1}.\n`;
  const line = "fixture output\n";
  const remaining = length - prefix.length;
  return prefix + line.repeat(Math.ceil(remaining / line.length)).slice(0, remaining);
}

function buildRenderedDiffHunk(index: number): DiffHunk {
  const lineCount = index < 13 ? 7 : 8;
  return {
    oldStart: 1,
    oldLines: 0,
    newStart: 1,
    newLines: lineCount,
    lines: Array.from(
      { length: lineCount },
      (_, lineIndex) => `+fixture line ${index + 1}.${lineIndex + 1}`,
    ),
  };
}
