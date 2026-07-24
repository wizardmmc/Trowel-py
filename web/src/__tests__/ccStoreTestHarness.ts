import { beforeEach, vi } from "vitest";
import type { AgentSession } from "../api/agent";
import type { AgentEvent } from "../api/agentTypes";

vi.mock("../api/agent", () => ({
  createAgentSession: vi.fn(),
  activateAgentSession: vi.fn().mockResolvedValue({ activeId: "s1" }),
  deleteAgentSession: vi.fn().mockResolvedValue({ closed: true }),
  listActiveAgentSessions: vi.fn(),
  listAgentHistory: vi.fn().mockResolvedValue({ rows: [], nextCursor: null }),
  listAgentRequests: vi.fn().mockResolvedValue([]),
  interruptAgentSession: vi.fn().mockResolvedValue({ interrupted: true }),
  answerAgentRequest: vi.fn(),
  getAgentHistory: vi.fn().mockResolvedValue([]),
  updateAgentSessionSettings: vi.fn(),
  agentMessagesUrl: (sid: string) => `/api/agent/sessions/${sid}/messages`,
}));

vi.mock("../api/cc", () => ({
  revertSession: vi.fn(),
  answerElicit: vi.fn(),
}));

export const stream = {
  apply: null as ((event: AgentEvent) => void) | null,
  resolvers: [] as Array<() => void>,
};

vi.mock("../api/ccStream", () => ({
  postMessageStream: vi.fn(
    (_url: string, _body: unknown, apply: (event: AgentEvent) => void) =>
      new Promise<void>((resolve) => {
        stream.apply = apply;
        stream.resolvers.push(resolve);
      }),
  ),
}));

import {
  answerAgentRequest,
  createAgentSession,
  deleteAgentSession,
  listActiveAgentSessions,
  listAgentHistory,
  updateAgentSessionSettings,
} from "../api/agent";

export const apiAnswerAgentRequest = vi.mocked(answerAgentRequest);
export const apiCreateSession = vi.mocked(createAgentSession);
export const apiDeleteSession = vi.mocked(deleteAgentSession);
export const listActiveSessions = vi.mocked(listActiveAgentSessions);
export const listHistory = vi.mocked(listAgentHistory);
export const apiUpdateSessionSettings = vi.mocked(updateAgentSessionSettings);

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
    turn_id: null,
    item_id: null,
    payload,
    ...over,
  };
}

export function mockCreate(sid: string, over: Partial<AgentSession> = {}): AgentSession {
  const session: AgentSession = {
    session_id: sid,
    runtime: "claude_code",
    native_session_id: null,
    workdir: "/wd",
    model: "glm-5.2",
    effort: null,
    permission: null,
    memory_enabled: true,
    profile_enabled: true,
    capabilities: ["tools", "approval", "checkpoint", "workflow"],
    name: sid,
    connected: false,
    running: false,
    ...over,
  };
  apiCreateSession.mockResolvedValueOnce(session);
  return session;
}

export async function releaseAllStreams(): Promise<void> {
  const resolvers = stream.resolvers;
  stream.resolvers = [];
  for (const resolve of resolvers) resolve();
  await Promise.resolve();
}

beforeEach(() => {
  vi.clearAllMocks();
  stream.apply = null;
  stream.resolvers = [];
  seqCounter = 0;
});
