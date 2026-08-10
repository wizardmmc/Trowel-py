/** 验证 turn、session 和 App 三层资源终态各自独立失败。 */

import { describe, expect, test } from "bun:test";
import {
  assertApplicationResourcesClosed,
  assertSessionResourcesClosed,
  assertTurnResourcesClosed,
  liveTurnResourceCount,
  redactIdentity,
} from "./resource-assertions.mjs";

describe("resource assertions", () => {
  const closed = (overrides = {}) => ({
    resource_kind: "turn_handle",
    owner_scope: "turn",
    owner_id: redactIdentity("turn-1"),
    state: "closed",
    ...overrides,
  });

  test("turn assertion ignores other owners and rejects its live handle", () => {
    expect(() =>
      assertTurnResourcesClosed(
        [closed({ owner_id: redactIdentity("turn-2"), state: "active" }), closed()],
        "turn-1",
      ),
    ).not.toThrow();
    expect(() =>
      assertTurnResourcesClosed([closed({ state: "active" })], "turn-1"),
    ).toThrow("turn still owns 1 resource");
    expect(liveTurnResourceCount([closed({ state: "active" })], "turn-1")).toBe(1);
  });

  test("session assertion rejects live session descendants", () => {
    expect(() =>
      assertSessionResourcesClosed(
        [
          closed({
            owner_scope: "session",
            owner_id: redactIdentity("session-1"),
            state: "active",
          }),
        ],
        "session-1",
      ),
    ).toThrow("session still owns 1 resource");
  });

  test("application assertion requires a verified zero marker", () => {
    expect(() =>
      assertApplicationResourcesClosed({ status: "closed", remaining_resource_count: 0 }),
    ).not.toThrow();
    expect(() =>
      assertApplicationResourcesClosed({ status: "needs_reconcile", remaining_resource_count: 1 }),
    ).toThrow("application still owns 1 resource");
  });
});
