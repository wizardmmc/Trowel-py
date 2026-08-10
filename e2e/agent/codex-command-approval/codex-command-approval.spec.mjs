/** AGT-001 / AGT-003：Codex command approval 拒绝与终态链。 */

import { seedRuntimeConfigurations } from "../../support/configuration-seed.mjs";
import {
  assertTurnClosed,
  closeAgentSessionFromUi,
  createAgentSessionFromUi,
  sendAgentTurn,
} from "../../support/agent-driver.mjs";
import { test, expect } from "../../support/fixtures.mjs";

test("AGT-001/003 Codex 审批使用原 request 并在取消后资源归零", async ({ desktop }) => {
  await seedRuntimeConfigurations(
    desktop.api,
    desktop.environment.modelCatalog.baseUrl,
    desktop.trace,
  );
  const session = await createAgentSessionFromUi(desktop, "codex");
  const turnId = await sendAgentTurn(desktop, "E2E_CODEX_APPROVAL");

  const approval = desktop.page.getByRole("region", {
    name: "Codex command approval pending",
  });
  await expect(approval).toBeVisible();
  await expect(desktop.page.getByRole("textbox", { name: "Agent 消息输入" })).toBeDisabled();
  const pending = await desktop.api.get(`/api/agent/sessions/${session.session_id}/requests`);
  expect(pending.requests).toHaveLength(1);
  expect(pending.requests[0]).toMatchObject({ status: "pending", approval_kind: "command_approval" });

  await approval.getByRole("button", { name: "取消本轮" }).click();
  await expect(desktop.page.getByText("已取消本轮")).toBeVisible();
  await assertTurnClosed(desktop, session.session_id, turnId);
  await closeAgentSessionFromUi(desktop, session.session_id);
});
