import { describe, expect, it } from "vitest";
import {
  apiCreateSession,
  apiUpdateSessionSettings,
  ev,
  listActiveSessions,
  mockCreate,
  releaseAllStreams,
  stream,
} from "./ccStoreTestHarness";
import { createCcStore } from "../stores/ccStore";

describe("createCcStore — backend session reconciliation", () => {
  it("pulls backend live sessions into the dict as connected rows", async () => {
    const store = createCcStore();
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
          capabilities: ["tools", "approval", "checkpoint", "workflow"],
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
          capabilities: ["tools", "approval", "checkpoint", "workflow"],
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
    expect(store.getState().sessions.s2.connected).toBe(true);
    expect(store.getState().activeSid).toBe("s1");
  });

  it("does NOT overwrite sessions the frontend already tracks", async () => {
    const store = createCcStore();
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
          capabilities: ["tools", "approval", "checkpoint", "workflow"],
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
    const store = createCcStore();
    listActiveSessions.mockRejectedValueOnce(new Error("backend down"));
    await store.getState().refreshActiveSessions();
    expect(store.getState().sessions).toEqual({});
  });
});

describe("createCcStore — memory/profile A/B switches", () => {
  it("startSession stores the condition from the backend response", async () => {
    const store = createCcStore();
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
          capabilities: ["tools", "approval", "checkpoint", "workflow"],
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

describe("createCcStore — Codex next-turn settings", () => {
  it("stores the backend-validated model/effort pair as pending", async () => {
    const store = createCcStore();
    mockCreate("s1", {
      runtime: "codex",
      model: "gpt-5.6-sol",
      effort: "low",
      capabilities: ["tools", "approval"],
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
    const store = createCcStore();
    mockCreate("s1", {
      runtime: "codex",
      model: "gpt-5.6-sol",
      effort: "low",
      capabilities: ["tools", "approval"],
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
    const store = createCcStore();
    mockCreate("s1", {
      runtime: "codex",
      permission_preset: "follow",
      capabilities: ["tools", "approval"],
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
