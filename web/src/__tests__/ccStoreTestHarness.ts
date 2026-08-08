import { beforeEach, vi } from "vitest";
import { getExpectedRuntimePresentation } from "../agent/runtimes";
import type { AgentSession } from "../agent/transport";
import type { AgentEvent } from "../agent/transport";
import type { AgentStreamControl } from "../agent/transport/stream";

vi.mock("../agent/transport/api", () => ({
  createAgentSession: vi.fn(),
  activateAgentSession: vi.fn().mockResolvedValue({ activeId: "s1" }),
  deleteAgentSession: vi.fn().mockResolvedValue({
    closed: true,
    status: "closed",
    remaining_resource_count: 0,
    remaining_resource_kinds: [],
    error: null,
  }),
  listActiveAgentSessions: vi.fn(),
  listAgentHistory: vi.fn().mockResolvedValue({ rows: [], nextCursor: null }),
  listAgentRequests: vi.fn().mockResolvedValue([]),
  getCodexGoal: vi.fn().mockResolvedValue(null),
  setCodexGoal: vi.fn(),
  clearCodexGoal: vi.fn().mockResolvedValue({ cleared: true }),
  startCodexTurn: vi.fn().mockResolvedValue({ turnId: "turn-1" }),
  startAgentTurn: vi.fn().mockResolvedValue({ turnId: "turn-1" }),
  compactCodexSession: vi.fn().mockResolvedValue({ started: true }),
  startCodexReview: vi.fn().mockResolvedValue({
    reviewThreadId: "thread-1",
    turnId: "review-turn-1",
  }),
  interruptAgentSession: vi.fn().mockResolvedValue({ interrupted: true }),
  answerAgentRequest: vi.fn(),
  getAgentHistory: vi.fn().mockResolvedValue([]),
  getCodexSubagentHistory: vi.fn().mockResolvedValue([]),
  updateAgentSessionSettings: vi.fn(),
  generateAgentSessionTitle: vi.fn(),
  renameAgentSessionTitle: vi.fn(),
  agentMessagesUrl: (sid: string) => `/api/agent/sessions/${sid}/messages`,
  agentEventsUrl: () => "/api/agent/events",
}));

vi.mock("../api/cc", () => ({
  revertSession: vi.fn(),
  answerElicit: vi.fn(),
}));

export const stream = {
  apply: null as ((event: AgentEvent) => void) | null,
  messageApply: null as ((event: AgentEvent) => void) | null,
  eventApply: null as ((event: AgentEvent) => void) | null,
  eventOpen: null as ((generation: string | null) => void) | null,
  eventControl: null as ((control: AgentStreamControl) => void) | null,
  messageResolvers: [] as Array<() => void>,
  eventResolvers: [] as Array<() => void>,
};

vi.mock("../agent/transport/stream", () => ({
  postMessageStream: vi.fn(
    (_url: string, _body: unknown, apply: (event: AgentEvent) => void) =>
      new Promise<void>((resolve) => {
        stream.apply = apply;
        stream.messageApply = apply;
        stream.messageResolvers.push(resolve);
      }),
  ),
  getEventStream: vi.fn(
    (
      _url: string,
      apply: (event: AgentEvent) => void,
      options?: {
        onOpen?: (generation: string | null) => void;
        onControl?: (control: AgentStreamControl) => void;
      },
    ) =>
      new Promise<void>((resolve) => {
        stream.apply = apply;
        stream.eventApply = apply;
        stream.messageApply = apply;
        stream.eventOpen = options?.onOpen ?? null;
        stream.eventControl = options?.onControl ?? null;
        stream.eventResolvers.push(resolve);
        options?.onOpen?.("generation-1");
      }),
  ),
}));

import {
  activateAgentSession,
  answerAgentRequest,
  createAgentSession,
  deleteAgentSession,
  getAgentHistory,
  getCodexSubagentHistory,
  listActiveAgentSessions,
  listAgentHistory,
  getCodexGoal,
  setCodexGoal,
  clearCodexGoal,
  startCodexTurn,
  startAgentTurn,
  compactCodexSession,
  startCodexReview,
  updateAgentSessionSettings,
  generateAgentSessionTitle,
  renameAgentSessionTitle,
} from "../agent/transport";
import { getEventStream } from "../agent/transport";
import { answerElicit, revertSession } from "../api/cc";

export const apiAnswerAgentRequest = vi.mocked(answerAgentRequest);
export const apiActivateAgentSession = vi.mocked(activateAgentSession);
export const apiCreateSession = vi.mocked(createAgentSession);
export const apiDeleteSession = vi.mocked(deleteAgentSession);
export const apiGetAgentHistory = vi.mocked(getAgentHistory);
export const apiGetCodexSubagentHistory = vi.mocked(getCodexSubagentHistory);
export const listActiveSessions = vi.mocked(listActiveAgentSessions);
export const listHistory = vi.mocked(listAgentHistory);
export const apiGetCodexGoal = vi.mocked(getCodexGoal);
export const apiSetCodexGoal = vi.mocked(setCodexGoal);
export const apiClearCodexGoal = vi.mocked(clearCodexGoal);
export const apiStartCodexTurn = vi.mocked(startCodexTurn);
export const apiStartAgentTurn = vi.mocked(startAgentTurn);
export const apiCompactCodexSession = vi.mocked(compactCodexSession);
export const apiStartCodexReview = vi.mocked(startCodexReview);
export const apiUpdateSessionSettings = vi.mocked(updateAgentSessionSettings);
export const apiGenerateSessionTitle = vi.mocked(generateAgentSessionTitle);
export const apiRenameSessionTitle = vi.mocked(renameAgentSessionTitle);
export const apiGetEventStream = vi.mocked(getEventStream);
export const apiAnswerElicit = vi.mocked(answerElicit);
export const apiRevertSession = vi.mocked(revertSession);

let seqCounter = 0;

export function ev(
  type: string,
  payload: Record<string, unknown> = {},
  over: Partial<AgentEvent> = {},
): AgentEvent {
  seqCounter += 1;
  return {
    schema: "agent-event-v1",
    session_id: "s1",
    runtime: "claude_code",
    seq: seqCounter,
    type,
    thread_id: null,
    turn_id: null,
    item_id: null,
    payload,
    ...over,
  };
}

export function mockCreate(
  sid: string,
  over: Partial<AgentSession> = {},
): AgentSession {
  const runtime = over.runtime ?? "claude_code";
  const session: AgentSession = {
    session_id: sid,
    runtime,
    native_session_id: null,
    workdir: "/wd",
    model: "glm-5.2",
    effort: null,
    permission: null,
    memory_enabled: true,
    profile_enabled: true,
    capabilities: getExpectedRuntimePresentation(runtime).expectedCapabilities,
    name: sid,
    connected: false,
    running: false,
    ...over,
  };
  apiCreateSession.mockResolvedValueOnce(session);
  return session;
}

export async function releaseAllStreams(): Promise<void> {
  // Claude 的当前 POST 流结束后，常驻事件流仍应继续监听自动续轮；Codex
  // 没有 POST 流时，该辅助函数才表示关闭它唯一的常驻流。
  const hasMessageStream = stream.messageResolvers.length > 0;
  const resolvers = hasMessageStream
    ? stream.messageResolvers
    : stream.eventResolvers;
  if (hasMessageStream) {
    stream.messageResolvers = [];
  } else {
    stream.eventResolvers = [];
  }
  for (const resolve of resolvers) resolve();
  await Promise.resolve();
}

export async function releaseMessageStreams(): Promise<void> {
  const resolvers = stream.messageResolvers;
  stream.messageResolvers = [];
  for (const resolve of resolvers) resolve();
  await Promise.resolve();
}

beforeEach(() => {
  vi.clearAllMocks();
  stream.apply = null;
  stream.messageApply = null;
  stream.eventApply = null;
  stream.eventOpen = null;
  stream.eventControl = null;
  stream.messageResolvers = [];
  stream.eventResolvers = [];
  seqCounter = 0;
  apiGenerateSessionTitle.mockImplementation(async (_sid, text) => ({
    ...mockAgentSessionForTitle(text),
  }));
  apiRenameSessionTitle.mockImplementation(async (_sid, title) => ({
    ...mockAgentSessionForTitle(title),
    display_title: title,
    title_source: "manual",
  }));
});

function mockAgentSessionForTitle(title: string): AgentSession {
  return {
    session_id: "s1",
    runtime: "claude_code",
    native_session_id: null,
    workdir: "/wd",
    model: "glm-5.2",
    effort: null,
    permission: null,
    memory_enabled: true,
    profile_enabled: true,
    capabilities: getExpectedRuntimePresentation("claude_code")
      .expectedCapabilities,
    name: "wd",
    connected: true,
    running: true,
    display_title: title,
    title_source: "generated",
  };
}
