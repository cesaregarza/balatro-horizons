import { test, expect } from "@playwright/test";

test("Stop stays usable with worker status hidden, after reload, without a status poll", async ({ page }) => {
  const statusPolls: string[] = [];
  let stops = 0;
  page.on("request", (request) => {
    if (request.url().endsWith("/api/operator/status")) statusPolls.push(request.url());
  });
  // A run may be active; the page cannot know without the exposure-recording status view.
  await page.route("**/api/stop", (route) => {
    stops += 1;
    return route.fulfill({ json: { stop_requested: true } });
  });
  await page.goto("/");
  await page.reload();
  await expect(page.getByText("Worker status hidden", { exact: true })).toBeVisible();
  const header = page.locator("header");
  const library = page.getByRole("region", { name: "Runs workspace" });
  for (const scope of [header, library]) {
    await expect(scope.getByRole("button", { name: "Stop worker", exact: true })).toBeEnabled();
  }
  // Worker state is unknown here, so no Stop control may claim the worker is idle.
  await expect(page.getByRole("button", { name: /idle/i })).toHaveCount(0);
  await header.getByRole("button", { name: "Stop worker", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Stop requested" })).toBeVisible();
  await library.getByRole("button", { name: "Stop worker", exact: true }).click();
  await expect.poll(() => stops).toBe(2);
  expect(statusPolls).toEqual([]);
});
