/** APP-001：验证 macOS 关窗驻留与最终 Quit 是两条不同的生命周期。 */

import { test, expect } from "../../support/fixtures.mjs";

test("APP-001 关窗驻留、重新打开并在最终 Quit 后资源归零", async ({ desktop }) => {
  await expect(desktop.page.getByRole("button", { name: "Agent" })).toBeVisible();
  await desktop.page.getByRole("button", { name: "Agent" }).click();
  await expect(desktop.page.getByRole("region", { name: "Agent 开始页" })).toBeVisible();

  await desktop.closeWindow();
  await expect.poll(() => desktop.electronApp.process().exitCode).toBeNull();

  await desktop.reopenWindow();
  await expect(desktop.page.getByRole("region", { name: "Agent 开始页" })).toBeVisible();

  const marker = await desktop.quit();
  expect(marker.status).toBe("closed");
  expect(marker.remaining_resource_count).toBe(0);
});
