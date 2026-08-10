/** AGT-001 / AGT-003：Claude AskUserQuestion 完整用户链。 */

import { seedRuntimeConfigurations } from "../../support/configuration-seed.mjs";
import {
  assertTurnClosed,
  closeAgentSessionFromUi,
  createAgentSessionFromUi,
  sendAgentTurn,
} from "../../support/agent-driver.mjs";
import { test, expect } from "../../support/fixtures.mjs";

test("AGT-001/003 Claude 提问回答后原 turn 结束且资源归零", async ({ desktop }) => {
  await seedRuntimeConfigurations(
    desktop.api,
    desktop.environment.modelCatalog.baseUrl,
    desktop.trace,
  );
  const session = await createAgentSessionFromUi(desktop, "claude_code");
  const turnId = await sendAgentTurn(desktop, "E2E_CLAUDE_ASK");

  await expect(desktop.page.getByText("Choose the deterministic E2E path?")).toBeVisible();
  const input = desktop.page.getByRole("textbox", { name: "Agent 消息输入" });
  await expect(input).toBeDisabled();
  await desktop.page.getByRole("option", { name: /Continue/ }).click();
  await desktop.page.getByRole("button", { name: "Submit", exact: true }).click();

  await expect(desktop.page.getByText("E2E_CLAUDE_COMPLETED")).toBeVisible();
  await assertTurnClosed(desktop, session.session_id, turnId);
  await closeAgentSessionFromUi(desktop, session.session_id);
});
