import { test, expect } from "@playwright/test";
import { explorerEpisode, mockExplorer } from "./explorerFixture";

test("ordinary detail is compact and technical records load only when opened", async ({ page }) => {
  await mockExplorer(page);
  const detailRequests: string[] = [];
  page.on("request", (request) => { if (/\/api\/explore\/decisions\/\d+(?:\?|$)/.test(request.url())) detailRequests.push(request.url()); });
  await page.goto(`/#explore/${explorerEpisode}/20`);
  await expect(page.getByRole("button", { name: "Annotate this decision" })).toBeVisible();
  expect(detailRequests).toHaveLength(1);
  expect(detailRequests[0]).toContain("technical=false");
  await page.getByText("Exact public decision records", { exact: true }).click();
  await expect.poll(() => detailRequests.length).toBe(2);
  expect(detailRequests[1]).not.toContain("technical=false");
});

test("ordinary history includes failed attempts and exact jumps keep the selected row visible", async ({ page }) => {
  await mockExplorer(page);
  await page.goto(`/#explore/${explorerEpisode}`);
  const list = page.getByRole("region", { name: "Recorded choices" });
  const detail = page.getByRole("region", { name: "Decision details" });
  await expect(list.locator("button")).toHaveCount(31);
  await expect(page.getByRole("region", { name: "Run API spend" })).toContainText("$0.42");
  await page.getByLabel("Go to decision", { exact: true }).fill("21");
  await page.getByRole("button", { name: "Go", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`#explore/${explorerEpisode}/20$`));
  await expect(detail.locator(".eyebrow")).toContainText("DECISION 21");
  await page.getByLabel("Search decisions", { exact: true }).fill("does not match");
  await expect(page.getByRole("heading", { name: "No matching decisions" })).toBeVisible();
  await expect(detail.locator(".eyebrow")).toContainText("DECISION 21");
  await expect(detail.getByText("Selected decision is outside the current filters")).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`#explore/${explorerEpisode}/20$`));
  await page.getByRole("button", { name: "Clear filters", exact: true }).click();
  const selected = list.locator('[aria-current="true"]');
  await expect(selected).toContainText("#21");
  await expect.poll(async () => selected.evaluate((row) => {
    const pane = row.closest(".decision-list")!.getBoundingClientRect();
    const bounds = row.getBoundingClientRect();
    return bounds.top >= pane.top && bounds.bottom <= pane.bottom;
  })).toBe(true);
  await page.getByRole("button", { name: "View failed decision" }).click();
  await expect(detail.locator(".eyebrow")).toContainText("DECISION 31");
  await expect(detail).toContainText("Decision ended without a game action");
  await expect(detail.getByRole("button", { name: "After decision", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Jump to latest decision" }).click();
  await expect(selected).toContainText("#31");
});

test("selection and filters survive tabs and browser history while polling pauses", async ({ page }) => {
  await page.clock.install();
  const fixture = await mockExplorer(page, { live: true });
  await page.goto(`/#explore/${explorerEpisode}`);
  const detail = page.getByRole("region", { name: "Decision details" });
  const list = page.getByRole("region", { name: "Recorded choices" });
  await expect(list.locator("button")).toHaveCount(31);
  await page.getByLabel("Search decisions", { exact: true }).fill("Recorded note");
  await list.getByRole("button", { name: /#21 / }).click();
  await expect(detail.locator(".eyebrow")).toContainText("DECISION 21");
  await page.getByRole("navigation").getByRole("button", { name: "Models & budgets" }).click();
  const pausedAt = fixture.polls();
  await page.clock.fastForward(8000);
  expect(fixture.polls()).toBe(pausedAt);
  await page.getByRole("navigation").getByRole("button", { name: "Decision explorer", exact: true }).click();
  await expect(page.getByLabel("Search decisions", { exact: true })).toHaveValue("Recorded note");
  await expect(detail.locator(".eyebrow")).toContainText("DECISION 21");
  await detail.getByRole("button", { name: "Next matching decision" }).click();
  await expect(detail.locator(".eyebrow")).toContainText("DECISION 22");
  await page.goBack();
  await expect(detail.locator(".eyebrow")).toContainText("DECISION 21");
  await page.goForward();
  await expect(detail.locator(".eyebrow")).toContainText("DECISION 22");
});

test("phone decision links open the requested board and return focus to its row", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockExplorer(page);
  await page.goto(`/#explore/${explorerEpisode}/20`);
  const detail = page.getByRole("region", { name: "Decision details" });
  const list = page.getByRole("region", { name: "Recorded choices" });
  await expect(detail).toBeVisible();
  await expect(detail.locator(".eyebrow")).toContainText("DECISION 21");
  await expect(list).toBeHidden();
  const money = detail.locator(".stats > div").filter({ hasText: "Money" }).locator("strong");
  await expect(money).toHaveText("$20");
  await expect(detail.locator("[data-decision-detail-heading]")).toBeInViewport();
  await detail.getByRole("button", { name: "After decision", exact: true }).click();
  await expect(money).toHaveText("$21");
  await detail.getByRole("button", { name: "Before decision", exact: true }).click();
  await expect(money).toHaveText("$20");
  await detail.getByRole("button", { name: "Back to choices" }).click();
  await expect(list).toBeVisible();
  await expect(list.locator('[aria-current="true"]')).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: test.info().outputPath("phone-decision-ux.png"), fullPage: false });
});

test("recovery eligibility is checked read-only and incompatible code has an actionable refusal", async ({ page }) => {
  const fixture = await mockExplorer(page);
  await page.goto(`/#explore/${explorerEpisode}`);
  await expect(page.getByText("Restore unavailable: The recorded game or agent code is incompatible with this release.")).toBeVisible();
  await expect(page.getByText(/Refreshing cannot make it compatible/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Restore run", exact: true })).toHaveCount(0);
  await expect(page.getByText("RESTORE_SOURCE_INCOMPATIBLE", { exact: true })).toBeHidden();
  await page.getByText("Technical reason", { exact: true }).click();
  await expect(page.getByText("RESTORE_SOURCE_INCOMPATIBLE", { exact: true })).toBeVisible();
  expect(fixture.previews()).toBe(1);
  expect(fixture.writes).toEqual([]);
});

test("completed games never advertise or check checkpoint recovery", async ({ page }) => {
  const fixture = await mockExplorer(page, { outcome: "WIN" });
  await page.goto(`/#explore/${explorerEpisode}`);
  await expect(page.getByRole("heading", { name: "Decision explorer", exact: true })).toBeVisible();
  await expect(page.getByText("Game completed · no checkpoint continuation needed.")).toBeVisible();
  await expect(page.getByRole("button", { name: /Restore/ })).toHaveCount(0);
  expect(fixture.previews()).toBe(0);
  expect(fixture.writes).toEqual([]);
});

test("busy recovery explains the wait and allows a new read-only check", async ({ page }) => {
  const fixture = await mockExplorer(page, { restore: { episode_id: explorerEpisode, available: false, reason: "WORKER_BUSY", plan: null } });
  await page.goto(`/#explore/${explorerEpisode}`);
  await expect(page.getByText("Restore unavailable: The worker is running another task.")).toBeVisible();
  await expect(page.getByText("Wait for that task to finish, then check again.")).toBeVisible();
  await page.getByRole("button", { name: "Check again", exact: true }).click();
  await expect.poll(fixture.previews).toBe(2);
  expect(fixture.writes).toEqual([]);
});
