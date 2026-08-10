/** DIS-005：共同公开后的历史、参与者详情和普通 Agent 交接。 */

import { seedRuntimeConfigurations } from "../../support/configuration-seed.mjs";
import { closeAgentSessionFromUi } from "../../support/agent-driver.mjs";
import {
  createThreeParticipantDiscussion,
  stopDiscussionAndAssertResources,
  waitForFirstPublication,
} from "../../support/discussion-driver.mjs";
import { test, expect } from "../../support/fixtures.mjs";

test("DIS-005 历史详情完整，并能交接到普通 Agent 后分别关闭", async ({ desktop }) => {
  await seedRuntimeConfigurations(
    desktop.api,
    desktop.environment.modelCatalog.baseUrl,
    desktop.trace,
  );
  const created = await createThreeParticipantDiscussion(desktop, "E2E_DISCUSSION_HANDOFF");
  await waitForFirstPublication(desktop, created.id);

  const history = desktop.page.getByRole("complementary", { name: "研讨历史" });
  await expect(history.getByRole("button", { name: /E2E_DISCUSSION_HANDOFF/ })).toBeVisible();
  desktop.trace.record("discussion.history_summary_visible");
  await desktop.electronApp.evaluate(({ BrowserWindow }) => {
    BrowserWindow.getAllWindows()[0]?.setSize(1024, 768);
  });
  await desktop.page.waitForFunction(() => globalThis.innerWidth <= 1180);
  desktop.trace.record("window.compact_layout_applied");
  const inspector = desktop.page.getByRole("complementary", { name: "参与者状态" });
  await expect(inspector).toBeHidden();
  const openInspector = desktop.page.getByRole("button", { name: "查看参与者配置" });
  await expect(openInspector).toBeVisible();
  await openInspector.click();
  await expect(inspector).toBeVisible();
  await expect(inspector).toHaveClass(/discussion-inspector--overlay/);
  await expect(
    desktop.page.getByRole("button", { name: "关闭配置" }),
  ).toBeVisible();
  await expect(inspector).toContainText("3 个原生会话");
  desktop.trace.record("discussion.participant_summary_visible", {
    display_mode: "overlay",
  });
  await inspector.getByRole("button", { name: "关闭参与者栏" }).click();
  await expect(inspector).toBeHidden();

  await desktop.page.getByRole("button", { name: "在 Agent 中继续" }).click();
  const handoff = desktop.page.getByRole("dialog", { name: "在 Agent 中继续" });
  await expect(handoff).toBeVisible();
  desktop.trace.record("discussion.handoff_dialog_opened");
  await handoff.getByRole("textbox", { name: "给 Agent 的指令" }).fill("E2E_HANDOFF");
  await handoff.getByRole("button", { name: "E2E Claude", exact: true }).click();
  await handoff.getByRole("button", { name: "创建并发送指令" }).click();
  await desktop.page.getByRole("textbox", { name: "Agent 消息输入" }).waitFor();
  desktop.trace.record("discussion.handoff_agent_opened");
  await expect(
    desktop.page.locator(".cc-view").getByText("E2E_CLAUDE_COMPLETED"),
  ).toBeVisible();
  desktop.trace.record("discussion.handoff_agent_completed");
  const active = await desktop.api.get("/api/agent/sessions/active");
  const agent = active.sessions.find((item) => item.session_id === active.active_id);
  expect(agent).toMatchObject({ runtime: "claude_code", session_kind: "user" });
  await closeAgentSessionFromUi(desktop, agent.session_id);

  await desktop.page.getByRole("button", { name: "研讨" }).click();
  desktop.trace.record("discussion.page_reopened");
  await history.getByRole("button", { name: /E2E_DISCUSSION_HANDOFF/ }).click();
  desktop.trace.record("discussion.history_reopened");
  await expect(desktop.page.getByRole("region", { name: "第 1 轮" })).toContainText(
    "3/3 同时公开",
  );
  desktop.trace.record("discussion.history_publication_visible");
  await stopDiscussionAndAssertResources(desktop, created.id);
});
