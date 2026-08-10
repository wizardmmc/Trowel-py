/** DIS-002：单 participant AskUserQuestion 等待、回答与共同发布。 */

import { seedRuntimeConfigurations } from "../../support/configuration-seed.mjs";
import {
  createThreeParticipantDiscussion,
  stopDiscussionAndAssertResources,
  waitForFirstPublication,
} from "../../support/discussion-driver.mjs";
import { test, expect } from "../../support/fixtures.mjs";

test("DIS-002 一位参与者等待回答时不提前公开，回答后 3/3 发布", async ({ desktop }) => {
  await seedRuntimeConfigurations(
    desktop.api,
    desktop.environment.modelCatalog.baseUrl,
    desktop.trace,
  );
  const created = await createThreeParticipantDiscussion(
    desktop,
    "E2E_DISCUSSION_APPROVAL",
  );
  await expect(desktop.page.getByText("Choose the deterministic E2E path?")).toBeVisible();
  const before = await desktop.api.get(`/api/discussions/${created.id}`);
  expect(before.status).toBe("running");
  expect(before.rounds[0].status).not.toBe("published");

  const questionCard = desktop.page
    .getByRole("region", { name: "第 1 轮" })
    .locator('article[data-status="running"]');
  await questionCard.getByRole("option", { name: /Continue/ }).click();
  await questionCard.getByRole("button", { name: "Submit", exact: true }).click();
  const published = await waitForFirstPublication(desktop, created.id);
  expect(published.rounds[0].participants.every((item) => item.status === "succeeded")).toBe(true);
  await stopDiscussionAndAssertResources(desktop, created.id);
});
