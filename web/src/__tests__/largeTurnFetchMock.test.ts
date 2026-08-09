/** 验证大回合性能页的假后端持续符合 Agent 前端读取契约。 */

import { afterEach, describe, expect, it } from "vitest";

import { installLargeTurnFetchMock } from "../perf/largeTurnFetchMock";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("large-turn fetch mock", () => {
  it("returns an empty Codex skill catalog instead of an untyped fallback", async () => {
    installLargeTurnFetchMock();

    const response = await fetch(
      "/api/agent/sessions/fixture-large-turn/skills",
    );

    await expect(response.json()).resolves.toMatchObject({
      success: true,
      data: { skills: [], errors: [] },
      error: null,
    });
  });
});
