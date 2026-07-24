import { describe, expect, it } from "vitest";
import { ev, mockCreate, releaseAllStreams, stream } from "./ccStoreTestHarness";
import { createCcStore, MAX_CONNECTIONS, MAX_RUNNING } from "../stores/ccStore";

describe("createCcStore — send admission", () => {
  it(`refuses send at MAX_RUNNING (${MAX_RUNNING}) concurrent streams`, async () => {
    const store = createCcStore();
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
    const store = createCcStore();
    for (let index = 0; index <= MAX_RUNNING; index += 1) {
      mockCreate(`s${index}`);
      await store.getState().startSession({ workdir: `/wd${index}` });
      const sending = store.getState().send("init");
      stream.apply!(ev("finished"));
      await releaseAllStreams();
      await sending;
    }

    const sends: Promise<unknown>[] = [];
    for (let index = 0; index <= MAX_RUNNING; index += 1) {
      await store.getState().activateSession(`s${index}`);
      sends.push(store.getState().send("burst"));
    }
    const sessions = Object.values(store.getState().sessions);
    expect(sessions.filter((session) => session.abort !== null)).toHaveLength(MAX_RUNNING);
    expect(
      sessions.filter(
        (session) => session.abort === null && session.transportError?.includes("in-turn"),
      ),
    ).toHaveLength(1);
    await releaseAllStreams();
    await Promise.all(sends);
  });

  it(`refuses send at MAX_CONNECTIONS (${MAX_CONNECTIONS}) connected`, async () => {
    const store = createCcStore();
    for (let index = 0; index < MAX_CONNECTIONS; index += 1) {
      mockCreate(`s${index}`);
      await store.getState().startSession({ workdir: `/wd${index}` });
      const sending = store.getState().send("x");
      stream.apply!(ev("finished"));
      await releaseAllStreams();
      await sending;
    }
    mockCreate("sX");
    await store.getState().startSession({ workdir: "/wdx" });
    await store.getState().send("y");
    const refused = store.getState().sessions.sX;
    expect(refused.connected).toBe(false);
    expect(refused.transportError).toMatch(/连接数已达上限/);
  });
});
