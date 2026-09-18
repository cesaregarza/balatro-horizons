import { expect, test } from "@playwright/test";

test("expired connection blocks native launch, reconnect restores it, synthetic stays available", async ({ page }) => {
  let ready = false;
  let launches = 0;
  await page.route("**/api/operator/runtime", (route) => route.fulfill({ json: {
    ready, code: ready ? null : "WINDOWS_SESSION_EXPIRED",
    message: ready ? "Windows connection registered" : "Windows runtime connection needs refreshing. Run scripts/configure_workbench_session.py --apply from a Windows-connected WSL terminal.",
  } }));
  await page.route("**/api/runs", (route) => {
    launches++;
    return route.fulfill({ json: { episode_id: "a".repeat(32) } });
  });
  await page.goto("/");
  await page.getByLabel("Synthetic pipeline test").uncheck();
  await expect(page.getByRole("button", { name: /Start native run/ })).toBeDisabled();
  await expect(page.getByText(/Windows runtime connection needs refreshing/)).toBeVisible();
  await page.getByLabel("Synthetic pipeline test").check();
  await expect(page.getByRole("button", { name: /Start test episode/ })).toBeEnabled();
  ready = true;
  await page.getByLabel("Synthetic pipeline test").uncheck();
  await expect(page.getByRole("button", { name: /Start native run/ })).toBeEnabled();
  expect(launches).toBe(0);
});
