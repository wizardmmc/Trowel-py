/** AGT-006 / SSE-001 / SSE-002：审批中重载 renderer 后恢复并对账同一 turn。 */

import { seedRuntimeConfigurations } from "../../support/configuration-seed.mjs";
import {
  assertTurnClosed,
  closeAgentSessionFromUi,
  createAgentSessionFromUi,
  sendAgentTurn,
} from "../../support/agent-driver.mjs";
import { test, expect } from "../../support/fixtures.mjs";

test("AGT-006/SSE-001/002 renderer 重载后恢复原审批且 history 不重复", async ({ desktop }) => {
  await seedRuntimeConfigurations(
    desktop.api,
    desktop.environment.modelCatalog.baseUrl,
    desktop.trace,
  );
  const session = await createAgentSessionFromUi(desktop, "codex");
  const turnId = await sendAgentTurn(desktop, "E2E_RENDERER_RELOAD");
  await expect(
    desktop.page.getByRole("region", { name: "Codex command approval pending" }),
  ).toBeVisible();

  await desktop.page.reload();
  await activateReloadedCodexSession(desktop);
  desktop.trace.record("renderer.reload_session_activated");
  const restoredApproval = desktop.page.getByRole("region", {
    name: "Codex command approval pending",
  });
  await expect(restoredApproval).toBeVisible();
  desktop.trace.record("approval.restored_after_reload");
  await expect(desktop.page.getByRole("textbox", { name: "Agent 消息输入" })).toBeDisabled();
  const pending = await desktop.api.get(`/api/agent/sessions/${session.session_id}/requests`);
  expect(pending.requests).toHaveLength(1);
  expect(pending.requests[0]).toMatchObject({ turn_id: turnId, status: "pending" });
  desktop.trace.record("approval.backend_pending_after_reload");

  desktop.trace.record("approval.answer_after_reload_started");
  const answerResponse = desktop.page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname.includes("/requests/") &&
      new URL(response.url()).pathname.endsWith("/answer"),
  );
  await restoredApproval.getByRole("button", { name: "取消本轮" }).click();
  const answerEnvelope = await (await answerResponse).json();
  desktop.trace.record("approval.answer_after_reload_completed", {
    success: answerEnvelope?.success === true,
    status: answerEnvelope?.data?.request?.status ?? "missing",
    decision: answerEnvelope?.data?.request?.decision ?? "missing",
    error_code: answerEnvelope?.error?.code ?? null,
  });
  await expect(desktop.page.getByText("已取消本轮")).toBeVisible();
  await assertTurnClosed(desktop, session.session_id, turnId);
  const history = await desktop.api.get(
    `/api/agent/sessions/${session.session_id}/history`,
  );
  expect(history.filter((event) => event.type === "user")).toHaveLength(1);
  expect(history.filter((event) => event.type === "finished")).toHaveLength(1);

  await desktop.page.reload();
  await activateReloadedCodexSession(desktop);
  await expect(desktop.page.getByText("已取消本轮")).toHaveCount(1);
  await closeAgentSessionFromUi(desktop, session.session_id);
});

/** 从重载后的多开栏重新选中仍由后端持有的 Codex 会话。 */
async function activateReloadedCodexSession(desktop) {
  await desktop.page.getByRole("button", { name: "Agent" }).click();
  desktop.trace.record("renderer.agent_page_opened_after_reload");
  const sessions = desktop.page.getByRole("complementary", { name: "多开会话" });
  const codexSession = sessions.getByRole("button").filter({ hasText: "E2E Codex" }).first();
  await codexSession.waitFor();
  desktop.trace.record("renderer.session_entry_found_after_reload");
  await codexSession.click();
  desktop.trace.record("renderer.session_entry_clicked_after_reload");
  await desktop.page.getByRole("textbox", { name: "Agent 消息输入" }).waitFor();
  desktop.trace.record("renderer.composer_ready_after_reload");
}
