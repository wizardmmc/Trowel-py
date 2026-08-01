import { describe, expect, it } from "vitest";
import {
  apiActivateAgentSession,
  apiCreateSession,
  apiUpdateSessionSettings,
  ev,
  listActiveSessions,
  mockCreate,
  releaseAllStreams,
  stream,
} from "./ccStoreTestHarness";
import { createAgentStore, type AgentSession } from "../agent";
import { getExpectedRuntimePresentation } from "../agent/runtimes";

const CC_CAPABILITIES = getExpectedRuntimePresentation("claude_code")
  .expectedCapabilities;
const CODEX_CAPABILITIES = getExpectedRuntimePresentation("codex")
  .expectedCapabilities;

/** 创建后端会话目录返回的已连接 CC 记录。 */
function liveSession(sessionId: string, workdir = "/wd"): AgentSession {
  return {
    session_id: sessionId,
    runtime: "claude_code",
    native_session_id: null,
    workdir,
    model: "glm-5.2",
    effort: null,
    permission: null,
    memory_enabled: true,
    profile_enabled: true,
    capabilities: CC_CAPABILITIES,
    name: sessionId,
    connected: true,
    running: false,
  };
}

/** 创建由测试控制完成时机的 Promise。 */
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("createAgentStore — backend session reconciliation", () => {
  it("pulls backend live sessions without adopting another client's active id", async () => {
    const store = createAgentStore();
    listActiveSessions.mockResolvedValueOnce({
      sessions: [
        {
          session_id: "s1",
          runtime: "claude_code",
          native_session_id: null,
          workdir: "/wd",
          model: "glm-5.2",
          effort: null,
          permission: null,
          memory_enabled: true,
          profile_enabled: true,
          capabilities: CC_CAPABILITIES,
          checkpoint_available: false,
          name: "trowel-py",
          connected: true,
          running: false,
        },
        {
          session_id: "s2",
          runtime: "claude_code",
          native_session_id: null,
          workdir: "/wd",
          model: "glm-5.2",
          effort: null,
          permission: null,
          memory_enabled: true,
          profile_enabled: true,
          capabilities: CC_CAPABILITIES,
          name: "wiki",
          connected: true,
          running: true,
        },
      ],
      activeId: "s1",
    });
    await store.getState().refreshActiveSessions();
    expect(store.getState().sessions.s1).toBeDefined();
    expect(store.getState().sessions.s1.connected).toBe(true);
    expect(store.getState().sessions.s1.checkpointAvailable).toBe(false);
    expect(store.getState().sessions.s2.connected).toBe(true);
    expect(store.getState().activeSid).toBeNull();
  });

  it("keeps session selection local to each renderer store", async () => {
    const sessions = [
      {
        session_id: "s1",
        runtime: "claude_code" as const,
        native_session_id: null,
        workdir: "/one",
        model: "glm-5.2",
        effort: null,
        permission: null,
        memory_enabled: true,
        profile_enabled: true,
        capabilities: CC_CAPABILITIES,
        name: "one",
        connected: true,
        running: false,
      },
      {
        session_id: "s2",
        runtime: "claude_code" as const,
        native_session_id: null,
        workdir: "/two",
        model: "glm-5.2",
        effort: null,
        permission: null,
        memory_enabled: true,
        profile_enabled: true,
        capabilities: CC_CAPABILITIES,
        name: "two",
        connected: true,
        running: false,
      },
    ];
    listActiveSessions
      .mockResolvedValueOnce({ sessions, activeId: "s1" })
      .mockResolvedValueOnce({ sessions, activeId: "s1" });
    const webStore = createAgentStore();
    const desktopStore = createAgentStore();
    await webStore.getState().refreshActiveSessions();
    await desktopStore.getState().refreshActiveSessions();

    await webStore.getState().activateSession("s1");
    await desktopStore.getState().activateSession("s2");

    expect(webStore.getState().activeSid).toBe("s1");
    expect(desktopStore.getState().activeSid).toBe("s2");
    expect(apiActivateAgentSession).not.toHaveBeenCalled();
  });

  it("does NOT overwrite sessions the frontend already tracks", async () => {
    const store = createAgentStore();
    mockCreate("s1");
    await store.getState().startSession({ workdir: "/wd" });
    const before = store.getState().sessions.s1;
    listActiveSessions.mockResolvedValueOnce({
      sessions: [
        {
          session_id: "s1",
          runtime: "claude_code",
          native_session_id: null,
          workdir: "/wd",
          model: "glm-5.2",
          effort: null,
          permission: null,
          memory_enabled: true,
          profile_enabled: true,
          capabilities: CC_CAPABILITIES,
          name: "renamed",
          connected: true,
          running: false,
        },
      ],
      activeId: "s1",
    });
    await store.getState().refreshActiveSessions();
    expect(store.getState().sessions.s1).toBe(before);
  });

  it("silently no-ops when the backend is unreachable", async () => {
    const store = createAgentStore();
    listActiveSessions.mockRejectedValueOnce(new Error("backend down"));
    await store.getState().refreshActiveSessions();
    expect(store.getState().sessions).toEqual({});
  });

  it("removes a user session closed by another renderer", async () => {
    const store = createAgentStore();
    listActiveSessions
      .mockResolvedValueOnce({ sessions: [liveSession("s1")], activeId: null })
      .mockResolvedValueOnce({ sessions: [], activeId: null });
    await store.getState().refreshActiveSessions();
    await store.getState().activateSession("s1");

    await store.getState().refreshActiveSessions();

    expect(store.getState().sessions.s1).toBeUndefined();
    expect(store.getState().activeSid).toBeNull();
  });

  it("ignores an older session catalog response that arrives last", async () => {
    const store = createAgentStore();
    const older = deferred<{
      readonly sessions: readonly AgentSession[];
      readonly activeId: string | null;
    }>();
    listActiveSessions
      .mockReturnValueOnce(older.promise)
      .mockResolvedValueOnce({
        sessions: [liveSession("newer", "/newer")],
        activeId: null,
      });

    const firstRefresh = store.getState().refreshActiveSessions();
    await store.getState().refreshActiveSessions();
    older.resolve({
      sessions: [liveSession("older", "/older")],
      activeId: null,
    });
    await firstRefresh;

    expect(Object.keys(store.getState().sessions)).toEqual(["newer"]);
  });

  it("ignores delegate rows if an old backend returns them", async () => {
    const store = createAgentStore();
    listActiveSessions.mockResolvedValueOnce({
      sessions: [
        {
          session_id: "delegate",
          runtime: "codex",
          native_session_id: "thread-delegate",
          workdir: "/wd",
          model: "gpt-5.6-sol",
          effort: "high",
          permission: null,
          memory_enabled: true,
          profile_enabled: true,
          capabilities: ["tools", "approval", "subagents"],
          name: "internal",
          connected: true,
          running: true,
          session_kind: "delegate",
        },
      ],
      activeId: "delegate",
    });

    await store.getState().refreshActiveSessions();

    expect(store.getState().sessions).toEqual({});
    expect(store.getState().activeSid).toBeNull();
  });
});

describe("createAgentStore — memory/profile A/B switches", () => {
  it("startSession stores the condition from the backend response", async () => {
    const store = createAgentStore();
    mockCreate("s1", { memory_enabled: false, profile_enabled: true });
    await store.getState().startSession({
      workdir: "/wd",
      memory_enabled: false,
      profile_enabled: true,
    });
    expect(store.getState().sessions.s1.memoryEnabled).toBe(false);
    expect(store.getState().sessions.s1.profileEnabled).toBe(true);
  });

  it("startSession forwards the switches to the API", async () => {
    const store = createAgentStore();
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
    const store = createAgentStore();
    listActiveSessions.mockResolvedValueOnce({
      sessions: [
        {
          session_id: "s1",
          runtime: "claude_code",
          native_session_id: null,
          workdir: "/wd",
          model: "m",
          effort: null,
          permission: null,
          memory_enabled: false,
          profile_enabled: true,
          capabilities: CC_CAPABILITIES,
          name: "wd",
          connected: true,
          running: false,
        },
      ],
      activeId: "s1",
    });
    await store.getState().refreshActiveSessions();
    expect(store.getState().sessions.s1.memoryEnabled).toBe(false);
    expect(store.getState().sessions.s1.profileEnabled).toBe(true);
  });
});

describe("createAgentStore — Codex next-turn settings", () => {
  it("stores the backend-validated model/effort pair as pending", async () => {
    const store = createAgentStore();
    mockCreate("s1", {
      runtime: "codex",
      model: "gpt-5.6-sol",
      effort: "low",
      capabilities: CODEX_CAPABILITIES,
    });
    apiUpdateSessionSettings.mockResolvedValueOnce({
      model: "gpt-5.6-luna",
      effort: "medium",
      adjusted: true,
    });
    await store.getState().startSession({ workdir: "/wd", runtime: "codex" });
    await store.getState().updateSessionSettings("gpt-5.6-luna", "ultra");
    const session = store.getState().sessions.s1;
    expect(apiUpdateSessionSettings).toHaveBeenCalledWith("s1", {
      model: "gpt-5.6-luna",
      effort: "ultra",
    });
    expect([session.pendingModel, session.pendingEffort]).toEqual([
      "gpt-5.6-luna",
      "medium",
    ]);
    expect(session.settingsNotice).toContain("已改为 medium");
  });

  it("commits and clears the pending pair only on model_changed", async () => {
    const store = createAgentStore();
    mockCreate("s1", {
      runtime: "codex",
      model: "gpt-5.6-sol",
      effort: "low",
      capabilities: CODEX_CAPABILITIES,
    });
    apiUpdateSessionSettings.mockResolvedValueOnce({
      model: "gpt-5.6-luna",
      effort: "medium",
      adjusted: false,
    });
    await store.getState().startSession({ workdir: "/wd", runtime: "codex" });
    await store.getState().updateSessionSettings("gpt-5.6-luna", "medium");
    const sending = store.getState().send("hi");
    stream.apply!(
      ev("model_changed", { model: "gpt-5.6-luna", effort: "medium" }, { runtime: "codex" }),
    );
    expect(store.getState().sessions.s1).toMatchObject({
      effort: "medium",
      pendingModel: null,
      pendingEffort: null,
      settingsNotice: null,
    });
    await releaseAllStreams();
    await sending;
  });

  it("applies native effective permission facts from lazy thread/start", async () => {
    const store = createAgentStore();
    mockCreate("s1", {
      runtime: "codex",
      permission_preset: "follow",
      capabilities: CODEX_CAPABILITIES,
    });
    await store.getState().startSession({ workdir: "/wd", runtime: "codex" });
    const sending = store.getState().send("hi");
    stream.apply!(
      ev(
        "session_started",
        {
          model: "gpt-5.6-sol",
          cwd: "/wd",
          cc_session_id: "thread-1",
          tools: [],
          permission_profile: ":read-only",
          effective_sandbox: "read-only",
          effective_approval: "on-request",
          network_access: false,
        },
        { runtime: "codex" },
      ),
    );
    expect(store.getState().sessions.s1).toMatchObject({
      nativeSessionId: "thread-1",
      permission: "Read only · on-request",
      permissionPreset: "follow",
      effectivePermissionProfile: ":read-only",
      effectiveSandbox: "read-only",
      effectiveApproval: "on-request",
      networkAccess: false,
    });
    await releaseAllStreams();
    await sending;
  });
});
