import { describe, expect, it } from "vitest";
import {
  apiGetEventStream,
  ev,
  listHistory,
  mockCreate,
  stream,
} from "./ccStoreTestHarness";
import {
  createAgentStore,
  MAX_CONNECTIONS,
  MAX_RUNNING,
} from "../agent/application";

describe("createAgentStore — send admission", () => {
  it(`refuses send at MAX_RUNNING (${MAX_RUNNING}) concurrent streams`, async () => {
    const store = createAgentStore();
    for (let index = 0; index < MAX_RUNNING; index += 1) {
      mockCreate(`s${index}`);
      await store.getState().startSession({ workdir: `/wd${index}` });
      void store.getState().send("x");
    }
    mockCreate("sX");
    await store.getState().startSession({ workdir: "/wdx" });
    await store.getState().send("y");
    const refused = store.getState().sessions.sX;
    expect(refused.abort).toBeNull();
    expect(refused.transportError).toMatch(/in-turn/);
  });

  it("MAX_RUNNING cap is atomic under a send burst (no race over-admission)", async () => {
    const store = createAgentStore();
    for (let index = 0; index <= MAX_RUNNING; index += 1) {
      mockCreate(`s${index}`);
      await store.getState().startSession({ workdir: `/wd${index}` });
      await store.getState().send("init");
      stream.apply!(
        ev("finished", {}, {
          session_id: `s${index}`,
          turn_id: "turn-1",
          seq: 1,
        }),
      );
    }

    const sends: Promise<unknown>[] = [];
    for (let index = 0; index <= MAX_RUNNING; index += 1) {
      await store.getState().activateSession(`s${index}`);
      sends.push(store.getState().send("burst"));
    }
    const sessions = Object.values(store.getState().sessions);
    expect(sessions.filter((session) => session.turnState === "running")).toHaveLength(MAX_RUNNING);
    expect(
      sessions.filter(
        (session) =>
          session.turnState !== "running" &&
          session.transportError?.includes("in-turn"),
      ),
    ).toHaveLength(1);
    await Promise.all(sends);
  });

  it(`refuses send at MAX_CONNECTIONS (${MAX_CONNECTIONS}) connected`, async () => {
    const store = createAgentStore();
    for (let index = 0; index < MAX_CONNECTIONS; index += 1) {
      mockCreate(`s${index}`);
      await store.getState().startSession({ workdir: `/wd${index}` });
      await store.getState().send("x");
      stream.apply!(
        ev("finished", {}, {
          session_id: `s${index}`,
          turn_id: "turn-1",
          seq: 1,
        }),
      );
    }
    mockCreate("sX");
    await store.getState().startSession({ workdir: "/wdx" });
    await store.getState().send("y");
    const refused = store.getState().sessions.sX;
    expect(refused.connected).toBe(false);
    expect(refused.transportError).toMatch(/连接数已达上限/);
  });

  it("delegate sessions do not consume the user running limit", async () => {
    const store = createAgentStore();
    for (let index = 0; index < MAX_RUNNING; index += 1) {
      mockCreate(`delegate-${index}`, { session_kind: "delegate" });
      await store.getState().startSession({ workdir: `/delegate-${index}` });
      void store.getState().send("background");
    }
    mockCreate("user");
    await store.getState().startSession({ workdir: "/user" });

    void store.getState().send("foreground");

    expect(store.getState().sessions.user.abort).not.toBeNull();
    expect(store.getState().sessions.user.transportError).toBeNull();
  });

  it("clears the previous structured problem when a retry is admitted", async () => {
    const store = createAgentStore();
    mockCreate("user");
    await store.getState().startSession({ workdir: "/user" });
    store.setState((state) => ({
      ...state,
      sessions: {
        ...state.sessions,
        user: {
          ...state.sessions.user,
          transportError: "previous request failed",
          transportProblem: {
            code: "http_error",
            message: "previous request failed",
            operation: "turn_start",
            budgetMs: 30_000,
            status: 409,
            occurredAt: "2026-08-05T00:00:00.000Z",
          },
        },
      },
    }));

    await store.getState().send("retry");

    expect(store.getState().sessions.user.transportError).toBeNull();
    expect(store.getState().sessions.user.transportProblem).toBeNull();
  });

  it("keeps one watcher and read APIs available with 20 connected and 5 running", async () => {
    const store = createAgentStore();
    for (let index = 0; index < MAX_CONNECTIONS; index += 1) {
      mockCreate(`s${index}`);
      await store.getState().startSession({ workdir: `/wd${index}` });
      await store.getState().send("materialize");
      stream.apply!(
        ev("finished", {}, {
          session_id: `s${index}`,
          turn_id: "turn-1",
          seq: 1,
        }),
      );
    }
    for (let index = 0; index < MAX_RUNNING; index += 1) {
      await store.getState().activateSession(`s${index}`);
      await store.getState().send("run");
    }

    await store.getState().refreshHistory("/wd0");
    await store.getState().activateSession(`s${MAX_RUNNING}`);
    await store.getState().send("sixth");

    expect(apiGetEventStream).toHaveBeenCalledTimes(1);
    expect(listHistory).toHaveBeenCalledWith("/wd0", { limit: 20 });
    expect(store.getState().sessions[`s${MAX_RUNNING}`].transportError).toMatch(
      /in-turn/,
    );
  });
});
