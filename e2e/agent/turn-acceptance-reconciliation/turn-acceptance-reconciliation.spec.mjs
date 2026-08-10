/** AGT-004：HTTP 回包丢失后按已接受 turn 对账，禁止自动重发和并发第二轮。 */

import { seedRuntimeConfigurations } from "../../support/configuration-seed.mjs";
import {
  assertTurnClosed,
  closeAgentSessionFromUi,
  createAgentSessionFromUi,
} from "../../support/agent-driver.mjs";
import { test, expect } from "../../support/fixtures.mjs";

test("AGT-004 发送接受结果未知时不重放，并保留原 turn 到终态", async ({ desktop }) => {
  await seedRuntimeConfigurations(
    desktop.api,
    desktop.environment.modelCatalog.baseUrl,
    desktop.trace,
  );
  const session = await createAgentSessionFromUi(desktop, "codex");
  let turnPostCount = 0;
  await desktop.page.route("**/api/agent/sessions/*/turns", async (route) => {
    turnPostCount += 1;
    if (turnPostCount === 1) {
      await route.fetch();
      await route.abort("failed");
      return;
    }
    await route.continue();
  });

  const input = desktop.page.getByRole("textbox", { name: "Agent 消息输入" });
  await input.fill("E2E_TURN_ACCEPTANCE_UNKNOWN");
  await input.press("Enter");
  const approval = desktop.page.getByRole("region", {
    name: "Codex command approval pending",
  });
  await expect(approval).toBeVisible();
  await expect(input).toBeDisabled();
  const active = await desktop.api.get("/api/agent/sessions/active");
  const current = active.sessions.find((item) => item.session_id === session.session_id);
  expect(current?.current_turn_id).toEqual(expect.any(String));

  await approval.getByRole("button", { name: "取消本轮" }).click();
  await expect(desktop.page.getByText("已取消本轮")).toBeVisible();
  await assertTurnClosed(
    desktop,
    current.session_id,
    current.current_turn_id,
  );
  const history = await desktop.api.get(
    `/api/agent/sessions/${session.session_id}/history`,
  );
  expect(turnPostCount).toBe(1);
  expect(history.filter((event) => event.type === "user")).toHaveLength(1);
  expect(history.filter((event) => event.type === "finished")).toHaveLength(1);
  expect(
    history
      .filter((event) => event.type === "user" || event.type === "finished")
      .every((event) => event.turn_id === current.current_turn_id),
  ).toBe(true);
  await closeAgentSessionFromUi(desktop, session.session_id);
});
