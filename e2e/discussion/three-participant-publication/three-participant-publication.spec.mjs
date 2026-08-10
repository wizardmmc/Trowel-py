/** DIS-001：三位可控 Claude participant 的第一轮共同发布。 */

import { seedRuntimeConfigurations } from "../../support/configuration-seed.mjs";
import {
  createThreeParticipantDiscussion,
  stopDiscussionAndAssertResources,
  waitForFirstPublication,
} from "../../support/discussion-driver.mjs";
import { test, expect } from "../../support/fixtures.mjs";

test("DIS-001 三位参与者形成一次 3/3 共同公开并收敛资源", async ({ desktop }) => {
  await seedRuntimeConfigurations(
    desktop.api,
    desktop.environment.modelCatalog.baseUrl,
    desktop.trace,
  );
  const created = await createThreeParticipantDiscussion(
    desktop,
    "E2E_DISCUSSION_SUCCESS",
  );
  expect(created.participants).toHaveLength(3);

  const published = await waitForFirstPublication(desktop, created.id);
  expect(published.rounds[0].participants.every((item) => item.status === "succeeded")).toBe(true);
  await expect(
    desktop.page
      .getByRole("complementary", { name: "研讨历史" })
      .getByRole("button", { name: /E2E_DISCUSSION_SUCCESS/ }),
  ).toBeVisible();

  const stopped = await stopDiscussionAndAssertResources(desktop, created.id);
  expect(stopped.status).toBe("stopped");
});
