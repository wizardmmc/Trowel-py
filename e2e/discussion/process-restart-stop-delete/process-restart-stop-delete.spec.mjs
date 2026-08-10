/** DIS-003 / DIS-004：进程中断后恢复同一轮，并完成 stop、delete 与资源核验。 */

import { seedRuntimeConfigurations } from "../../support/configuration-seed.mjs";
import {
  createThreeParticipantDiscussion,
  stopDiscussionAndAssertResources,
} from "../../support/discussion-driver.mjs";
import { test, expect } from "../../support/fixtures.mjs";

test("DIS-003/004 进程重启只重跑未完成槽位，随后停止删除并归零", async ({ desktop }) => {
  await seedRuntimeConfigurations(
    desktop.api,
    desktop.environment.modelCatalog.baseUrl,
    desktop.trace,
  );
  const created = await createThreeParticipantDiscussion(
    desktop,
    "E2E_DISCUSSION_PROCESS_RESTART",
  );
  let beforeCrash;
  await expect
    .poll(async () => {
      beforeCrash = await desktop.api.get(`/api/discussions/${created.id}`);
      return (beforeCrash.rounds[0]?.participants ?? [])
        .map((item) => item.status)
        .sort()
        .join(",") || "not-started";
    })
    .toBe("running,running,sealed");
  await expect(
    desktop.page.getByRole("region", { name: "第 1 轮" }).locator('b[data-published="true"]'),
  ).toHaveCount(0);
  const originalAttempts = new Map(
    beforeCrash.rounds[0].participants.map((item) => [item.participant_id, item.current_attempt_id]),
  );
  const alreadySucceeded = beforeCrash.rounds[0].participants.find(
    (item) => item.status === "sealed",
  );

  await desktop.crashAndRestart();
  let recovered;
  await expect
    .poll(async () => {
      recovered = await desktop.api.get(`/api/discussions/${created.id}`);
      return `${recovered.status}:${recovered.rounds[0].status}`;
    })
    .toBe("waiting_user:published");
  expect(recovered.rounds).toHaveLength(1);
  expect(JSON.stringify(recovered)).not.toContain("E2E_OLD_PARTIAL_MUST_NOT_PUBLISH");
  for (const participant of recovered.rounds[0].participants) {
    const originalAttempt = originalAttempts.get(participant.participant_id);
    if (participant.participant_id === alreadySucceeded.participant_id) {
      expect(participant.current_attempt_id).toBe(originalAttempt);
    } else {
      expect(participant.current_attempt_id).not.toBe(originalAttempt);
    }
  }

  await desktop.page.getByRole("button", { name: "研讨" }).click();
  const history = desktop.page.getByRole("complementary", { name: "研讨历史" });
  await history.getByRole("button", { name: /E2E_DISCUSSION_PROCESS_RESTART/ }).click();
  await expect(desktop.page.getByRole("region", { name: "第 1 轮" })).toContainText(
    "3/3 同时公开",
  );
  await stopDiscussionAndAssertResources(desktop, created.id);

  const deleteResponse = desktop.page.waitForResponse(
    (response) =>
      response.request().method() === "DELETE" &&
      new URL(response.url()).pathname.endsWith(`/discussions/${created.id}`),
  );
  await desktop.page.getByRole("button", { name: "删除研讨" }).click();
  const confirmation = desktop.page.getByRole("alertdialog", { name: "删除这场研讨？" });
  await confirmation.getByRole("button", { name: "删除研讨" }).click();
  const envelope = await (await deleteResponse).json();
  expect(envelope?.data).toMatchObject({ id: created.id, deleted: true });
  await expect
    .poll(async () => (await desktop.api.get("/api/discussions")).some((item) => item.id === created.id))
    .toBe(false);
});
