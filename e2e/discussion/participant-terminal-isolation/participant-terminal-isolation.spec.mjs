/** DIS-002：失败和限额槽位不阻塞另外两位 participant 的共同发布。 */

import { seedRuntimeConfigurations } from "../../support/configuration-seed.mjs";
import {
  createThreeParticipantDiscussion,
  stopDiscussionAndAssertResources,
  waitForFirstPublication,
} from "../../support/discussion-driver.mjs";
import { test, expect } from "../../support/fixtures.mjs";

for (const scenario of [
  { topic: "E2E_DISCUSSION_ERROR", terminal: "failed", label: "失败" },
  { topic: "E2E_DISCUSSION_LIMIT", terminal: "limited", label: "额度不足" },
]) {
  test(`DIS-002 单 participant ${scenario.terminal} 后其余结果仍共同公开`, async ({ desktop }) => {
    await seedRuntimeConfigurations(
      desktop.api,
      desktop.environment.modelCatalog.baseUrl,
      desktop.trace,
    );
    const created = await createThreeParticipantDiscussion(desktop, scenario.topic);
    const published = await waitForFirstPublication(desktop, created.id);
    const statuses = published.rounds[0].participants.map((item) => item.status).sort();
    expect(statuses).toEqual([scenario.terminal, "succeeded", "succeeded"].sort());
    await expect(
      desktop.page
        .getByRole("region", { name: "第 1 轮" })
        .locator(`article[data-status="${scenario.terminal}"]`),
    ).toContainText(scenario.label);
    await stopDiscussionAndAssertResources(desktop, created.id);
  });
}
