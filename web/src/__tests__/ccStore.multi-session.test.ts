/**
 * slice-028 D2 multi-session store tests (v2 model).
 *
 * v2 model: a session is a "connection" only once send() spawns the cc
 * subprocess (connected=true). "+" / load-history states (connected=false)
 * don't appear in the multi-session bar and are dropped when switched away
 * ("切走即丢"). session_exited REMOVES the row (never greyed). Caps count
 * connected sessions and fire on send.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("../api/agent", () => ({
  createAgentSession: vi.fn(),
  activateAgentSession: vi.fn().mockResolvedValue({ activeId: "s1" }),
  deleteAgentSession: vi.fn().mockResolvedValue({ closed: true }),
  listActiveAgentSessions: vi.fn(),
  listAgentHistory: vi.fn().mockResolvedValue([]),
  interruptAgentSession: vi.fn().mockResolvedValue({ interrupted: true }),
  getAgentHistory: vi.fn().mockResolvedValue([]),
  agentMessagesUrl: (sid: string) => `/api/agent/sessions/${sid}/messages`,
}));

vi.mock("../api/cc", () => ({
  revertSession: vi.fn(),
  answerElicit: vi.fn(),
}));

// ccStream: capture the apply callback + hold the stream OPEN until released.
let streamApply: ((ev: AgentEvent) => void) | null = null;
let streamResolvers: (() => void)[] = [];
vi.mock("../api/ccStream", () => ({
  postMessageStream: vi.fn(
    (_url: string, _body: unknown, apply: (ev: AgentEvent) => void) =>
      new Promise<void>((resolve) => {
        streamApply = apply;
        streamResolvers.push(resolve);
      }),
  ),
}));

import {
  createAgentSession as apiCreateSession,
  deleteAgentSession as apiDeleteSession,
  listActiveAgentSessions as listActiveSessions,
} from "../api/agent";
import { createCcStore, MAX_RUNNING, MAX_CONNECTIONS } from "../stores/ccStore";
import type { AgentSession } from "../api/agent";
import type { AgentEvent } from "../api/agentTypes";

/** slice-074: build an AgentEvent v1 envelope with an auto-incrementing seq.
 * Tests feed envelopes (not flat events) to applyTo, matching the real wire. */
let seqCounter = 0;
function ev(
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

function mockCreate(sid: string, over: Partial<AgentSession> = {}): AgentSession {
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
  vi.mocked(apiCreateSession).mockResolvedValueOnce(session);
  return session;
}

async function releaseAllStreams(): Promise<void> {
  const resolvers = streamResolvers;
  streamResolvers = [];
  for (const r of resolvers) r();
  await Promise.resolve();
}

beforeEach(() => {
  vi.clearAllMocks();
  streamApply = null;
  streamResolvers = [];
  seqCounter = 0;
});

describe("createCcStore — multi-session v2 (connected model)", () => {
  it("startSession creates a session that is NOT yet connected (not in the bar)", async () => {
    const store = createCcStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    const s = store.getState();
    expect(s.activeSid).toBe("s1");
    expect(s.sessions["s1"]).toBeDefined();
    expect(s.sessions["s1"].connected).toBe(false);
  });

  it("send() flips the session to connected (enters the bar)", async () => {
    const store = createCcStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    expect(store.getState().sessions["s1"].connected).toBe(false);
    const p = store.getState().send("hi");
    expect(store.getState().sessions["s1"].connected).toBe(true);
    streamApply!(ev("finished"));
    await releaseAllStreams();
    await p;
  });

  it("two CONNECTED sessions coexist; switching preserves both", async () => {
    const store = createCcStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/a" });
    const p1 = store.getState().send("one");
    streamApply!(ev("finished"));
    await releaseAllStreams();
    await p1;
    mockCreate("s2");
    await store.getState().startSession({ workdir: "/b" });
    const p2 = store.getState().send("two");
    streamApply!(ev("finished"));
    await releaseAllStreams();
    await p2;
    // both connected, s2 active
    expect(store.getState().sessions["s1"].connected).toBe(true);
    expect(store.getState().sessions["s2"].connected).toBe(true);
    expect(store.getState().activeSid).toBe("s2");

    await store.getState().activateSession("s1");
    expect(store.getState().activeSid).toBe("s1");
    expect(store.getState().sessions["s2"]).toBeDefined(); // preserved
  });

  it("switching away from a never-connected temp drops it (切走即丢)", async () => {
    const store = createCcStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/a" });
    // connect s1 so it survives the next startSession
    const p1 = store.getState().send("one");
    streamApply!(ev("finished"));
    await releaseAllStreams();
    await p1;
    mockCreate("s2");
    await store.getState().startSession({ workdir: "/b" }); // s2 temp, s1 kept (connected)
    expect(store.getState().activeSid).toBe("s2");
    await store.getState().activateSession("s1"); // s2 (temp) dropped
    expect(store.getState().sessions["s2"]).toBeUndefined();
    expect(store.getState().sessions["s1"]).toBeDefined();
    expect(apiDeleteSession).toHaveBeenCalledWith("s2");
  });

  it("startSession also drops a never-connected temp active", async () => {
    const store = createCcStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/a" }); // s1 temp active
    mockCreate("s2");
    await store.getState().startSession({ workdir: "/b" }); // drops s1 (temp), s2 active
    expect(store.getState().sessions["s1"]).toBeUndefined();
    expect(store.getState().sessions["s2"]).toBeDefined();
    expect(store.getState().activeSid).toBe("s2");
    expect(apiDeleteSession).toHaveBeenCalledWith("s1");
  });

  it("Q4: send routes events to the session that opened the stream, not the active one", async () => {
    const store = createCcStore();
    // two connected sessions
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/a" });
    const p1 = store.getState().send("one");
    streamApply!(ev("finished"));
    await releaseAllStreams();
    await p1;
    mockCreate("s2");
    await store.getState().startSession({ workdir: "/b" });
    const p2 = store.getState().send("two");
    streamApply!(ev("finished"));
    await releaseAllStreams();
    await p2;

    // switch back to s1 and start a stream; s2 is connected so it survives
    await store.getState().activateSession("s1");
    const sendPromise = store.getState().send("hi");
    expect(streamApply).not.toBeNull();

    // pump a text event (routes by captured sid = s1)
    streamApply!(ev("text", { text: "chunk" }));
    // switch to s2 mid-stream; s1's in-flight text still lands on s1
    await store.getState().activateSession("s2");
    streamApply!(ev("finished"));
    await releaseAllStreams();
    await sendPromise;

    const s1 = store.getState().sessions["s1"];
    const s2 = store.getState().sessions["s2"];
    expect(s1.turns[s1.turns.length - 1].items.length).toBeGreaterThan(0);
    expect(s2.turns.length).toBe(1); // s2 has its own "two" turn, untouched by s1's stream
  });

  it("refuses a second concurrent send into the same session", async () => {
    const store = createCcStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    const first = store.getState().send("one");
    await store.getState().send("two"); // no-op (abort set)
    streamApply!(ev("finished"));
    await releaseAllStreams();
    await first;
    expect(store.getState().sessions["s1"].turns).toHaveLength(1);
  });

  it("session_exited REMOVES the row (no grey/resumable) + clears activeSid", async () => {
    const store = createCcStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    const p = store.getState().send("/exit");
    streamApply!(ev("finished"));
    streamApply!(ev("session_exited", { returncode: 0 }));
    await releaseAllStreams();
    await p;
    const s = store.getState();
    expect(s.sessions["s1"]).toBeUndefined(); // removed entirely
    expect(s.activeSid).toBeNull();
  });

  it("closeSession removes the row + drops activeSid for the active one", async () => {
    const store = createCcStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    await store.getState().closeSession("s1");
    expect(store.getState().sessions["s1"]).toBeUndefined();
    expect(store.getState().activeSid).toBeNull();
    expect(apiDeleteSession).toHaveBeenCalledWith("s1");
  });

  it(`refuses send at MAX_RUNNING (${MAX_RUNNING}) concurrent streams`, async () => {
    const store = createCcStore();
    for (let i = 0; i < MAX_RUNNING; i++) {
      mockCreate(`s${i}`);
      await store.getState().startSession({ workdir: `/wd${i}` });
      void store.getState().send("x");
    }
    mockCreate(`sX`);
    await store.getState().startSession({ workdir: "/wdx" });
    await store.getState().send("y");
    const sX = store.getState().sessions["sX"];
    expect(sX.abort).toBeNull();
    expect(sX.transportError).toMatch(/in-turn/);
  });

  it("MAX_RUNNING cap is atomic under a send burst (no race over-admission)", async () => {
    const store = createCcStore();
    // create + connect MAX_RUNNING+1 sessions (each connected+idle, abort=null)
    for (let i = 0; i <= MAX_RUNNING; i++) {
      mockCreate(`s${i}`);
      await store.getState().startSession({ workdir: `/wd${i}` });
      const p = store.getState().send("init");
      streamApply!(ev("finished"));
      await releaseAllStreams();
      await p;
    }
    // fire concurrent re-sends on each (activate then send, no await between)
    const sendPromises: Promise<unknown>[] = [];
    for (let i = 0; i <= MAX_RUNNING; i++) {
      await store.getState().activateSession(`s${i}`);
      sendPromises.push(store.getState().send("burst"));
    }
    const sessions = store.getState().sessions;
    const admitted = Object.values(sessions).filter((s) => s.abort !== null);
    expect(admitted.length).toBe(MAX_RUNNING);
    const refused = Object.values(sessions).filter(
      (s) => s.abort === null && s.transportError?.includes("in-turn"),
    );
    expect(refused.length).toBe(1);
    await releaseAllStreams();
    await Promise.all(sendPromises);
  });

  it(`refuses send at MAX_CONNECTIONS (${MAX_CONNECTIONS}) connected`, async () => {
    const store = createCcStore();
    // create + send on MAX_CONNECTIONS sessions → all connected
    for (let i = 0; i < MAX_CONNECTIONS; i++) {
      mockCreate(`s${i}`);
      await store.getState().startSession({ workdir: `/wd${i}` });
      const p = store.getState().send("x");
      streamApply!(ev("finished"));
      await releaseAllStreams();
      await p;
    }
    // create one more temp + try to send → refused (would be the 21st connection)
    mockCreate(`sX`);
    await store.getState().startSession({ workdir: "/wdx" });
    await store.getState().send("y");
    const sX = store.getState().sessions["sX"];
    expect(sX.connected).toBe(false);
    expect(sX.transportError).toMatch(/连接数已达上限/);
  });

  it("reset clears all sessions + activeSid", async () => {
    const store = createCcStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    store.getState().reset();
    const s = store.getState();
    expect(s.sessions).toEqual({});
    expect(s.activeSid).toBeNull();
  });

  it("slice-072: Codex host_status(host_exited) keeps the row (binding survives, spec §4)", async () => {
    // host_exited is a TURN terminal, not a row exit — the binding survives so
    // the next send can resume. The row stays; only the running turn errors.
    const store = createCcStore();
    mockCreate("c1", { runtime: "codex" as never });
    await store.getState().startSession({ workdir: "/wd", runtime: "codex" });
    const p = store.getState().send("hi");
    streamApply!(ev("host_status", { status: "host_exited" }, { runtime: "codex" }));
    await releaseAllStreams();
    await p;
    expect(store.getState().sessions["c1"]).toBeDefined(); // row kept
    expect(store.getState().activeSid).toBe("c1"); // still active
    expect(store.getState().sessions["c1"]?.phase).toBe("error"); // turn errored
  });

  describe("refreshActiveSessions (reload reconcile)", () => {
    it("pulls backend live sessions into the dict as connected rows", async () => {
      const store = createCcStore();
      vi.mocked(listActiveSessions).mockResolvedValueOnce({
        sessions: [
          { session_id: "s1", runtime: "claude_code", native_session_id: null, workdir: "/wd", model: "glm-5.2", effort: null, permission: null, memory_enabled: true, profile_enabled: true, capabilities: ["tools", "approval", "checkpoint", "workflow"], name: "trowel-py", connected: true, running: false },
          { session_id: "s2", runtime: "claude_code", native_session_id: null, workdir: "/wd", model: "glm-5.2", effort: null, permission: null, memory_enabled: true, profile_enabled: true, capabilities: ["tools", "approval", "checkpoint", "workflow"], name: "wiki", connected: true, running: true },
        ],
        activeId: "s1",
      });
      await store.getState().refreshActiveSessions();
      const s = store.getState();
      expect(s.sessions["s1"]).toBeDefined();
      expect(s.sessions["s1"].connected).toBe(true);
      expect(s.sessions["s2"].connected).toBe(true);
      expect(s.activeSid).toBe("s1"); // backend's active_id adopted
    });

    it("does NOT overwrite sessions the frontend already tracks", async () => {
      const store = createCcStore();
      mockCreate("s1");
      await store.getState().startSession({ workdir: "/wd" });
      const before = store.getState().sessions["s1"];
      vi.mocked(listActiveSessions).mockResolvedValueOnce({
        sessions: [{ session_id: "s1", runtime: "claude_code", native_session_id: null, workdir: "/wd", model: "glm-5.2", effort: null, permission: null, memory_enabled: true, profile_enabled: true, capabilities: ["tools", "approval", "checkpoint", "workflow"], name: "renamed", connected: true, running: false }],
        activeId: "s1",
      });
      await store.getState().refreshActiveSessions();
      // frontend's existing record wins (name not overwritten)
      expect(store.getState().sessions["s1"]).toBe(before);
    });

    it("silently no-ops when the backend is unreachable", async () => {
      const store = createCcStore();
      vi.mocked(listActiveSessions).mockRejectedValueOnce(new Error("backend down"));
      await store.getState().refreshActiveSessions();
      expect(store.getState().sessions).toEqual({});
    });
  });

  describe("slice-060: memory/profile A/B switches", () => {
    it("startSession stores the condition from the backend response", async () => {
      const store = createCcStore();
      mockCreate("s1", { memory_enabled: false, profile_enabled: true });
      await store.getState().startSession({
        workdir: "/wd",
        memory_enabled: false,
        profile_enabled: true,
      });
      const s = store.getState().sessions["s1"];
      expect(s?.memoryEnabled).toBe(false);
      expect(s?.profileEnabled).toBe(true);
    });

    it("startSession forwards the switches to the API", async () => {
      const store = createCcStore();
      mockCreate("s1");
      await store.getState().startSession({
        workdir: "/wd",
        memory_enabled: false,
        profile_enabled: false,
      });
      expect(apiCreateSession).toHaveBeenCalledWith(
        expect.objectContaining({
          memory_enabled: false,
          profile_enabled: false,
        }),
      );
    });

    it("refreshActiveSessions reconciles the condition from the backend", async () => {
      const store = createCcStore();
      vi.mocked(listActiveSessions).mockResolvedValueOnce({
        sessions: [
          { session_id: "s1", runtime: "claude_code", native_session_id: null, workdir: "/wd", model: "m", effort: null, permission: null, memory_enabled: false, profile_enabled: true, capabilities: ["tools", "approval", "checkpoint", "workflow"], name: "wd", connected: true, running: false },
        ],
        activeId: "s1",
      });
      await store.getState().refreshActiveSessions();
      const s = store.getState().sessions["s1"];
      expect(s?.memoryEnabled).toBe(false); // backend value, not a local default
      expect(s?.profileEnabled).toBe(true);
    });
  });

  it("tasks are per-session (switching does not leak task lists)", async () => {
    const store = createCcStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    const p = store.getState().send("do it");
    streamApply!(ev("tool_call", { tool_use_id: "tu_1", tool_name: "TaskCreate", input: { subject: "s1 task" } }));
    streamApply!(ev("finished"));
    await releaseAllStreams();
    await p;
    expect(store.getState().sessions["s1"].tasks).toHaveLength(1);

    mockCreate("s2");
    await store.getState().startSession({ workdir: "/wd" });
    expect(store.getState().sessions["s2"].tasks).toHaveLength(0);
    expect(store.getState().sessions["s1"].tasks).toHaveLength(1);
  });
});

describe("slice-074: per-session seq contract (dup drop + gap flag)", () => {
  it("drops a duplicate seq (re-delivered event does not double-append)", async () => {
    const store = createCcStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    const p = store.getState().send("hi");
    streamApply!(ev("text", { text: "a" })); // seq 1
    streamApply!(ev("text", { text: "b" }, { seq: 1 })); // dup seq → dropped
    streamApply!(ev("finished")); // seq 3 (counter continues; gap from the dup's perspective is irrelevant)
    await releaseAllStreams();
    await p;
    const turn = store.getState().sessions["s1"].turns[0];
    // Only one text item ("a"); the duplicate "b" was dropped.
    expect(turn.items.filter((i) => i.kind === "text")).toHaveLength(1);
    expect((turn.items[0] as { text: string }).text).toBe("a");
  });

  it("flags needsReplay when a seq gap is observed", async () => {
    const store = createCcStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    const p = store.getState().send("hi");
    streamApply!(ev("text", { text: "a" })); // seq 1
    streamApply!(ev("text", { text: "b" }, { seq: 5 })); // gap 1→5
    streamApply!(ev("finished"));
    await releaseAllStreams();
    await p;
    expect(store.getState().sessions["s1"].needsReplay).toBe(true);
  });

  it("seq is per-session (two streams do not share the counter)", async () => {
    // Each session's adapter stamps its own seq from 1; the store tracks them
    // independently, so a seq-2 on session B does not collide with session A.
    const store = createCcStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/a" });
    const p1 = store.getState().send("one");
    streamApply!(ev("text", { text: "a" })); // s1 seq 1
    streamApply!(ev("finished")); // s1 seq 2
    await releaseAllStreams();
    await p1;
    mockCreate("s2");
    await store.getState().startSession({ workdir: "/b" });
    const p2 = store.getState().send("two");
    streamApply!(ev("text", { text: "b" }, { seq: 1, session_id: "s2" })); // s2 seq 1 — independent
    streamApply!(ev("finished", {}, { seq: 2, session_id: "s2" }));
    await releaseAllStreams();
    await p2;
    expect(store.getState().sessions["s1"].lastSeq).toBe(2);
    expect(store.getState().sessions["s2"].lastSeq).toBe(2);
    expect(store.getState().sessions["s2"].needsReplay).toBe(false);
  });
});
