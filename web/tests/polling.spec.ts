import { test, expect } from "@playwright/test";

test("slow status requests never overlap and hiding status stops polling", async ({
  page,
}) => {
  let requests = 0;
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/operator/status", async (route) => {
    requests += 1;
    await held;
    await route.fulfill({
      json: { running: false, active_episode: null, error: null, episodes: [] },
    });
  });
  await page.goto("/");
  await page
    .getByRole("button", { name: "Watch live status", exact: true })
    .click();
  await expect.poll(() => requests).toBe(1);
  // Longer than two polling intervals: the old setInterval accumulated requests.
  await page.waitForTimeout(4300);
  expect(requests).toBe(1);
  release();
  await expect.poll(() => requests).toBe(2);
  await page
    .getByRole("button", { name: "Hide live status", exact: true })
    .click();
  await page.waitForTimeout(2300);
  expect(requests).toBe(2);
});
