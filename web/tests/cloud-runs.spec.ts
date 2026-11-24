import { expect, test, type Page } from "@playwright/test";

const longRunId = "run-with-a-very-long-identifier-that-would-widen-a-table-0123456789";
const report = {
  read_at: "2026-11-24T18:30:05+00:00",
  launches: "REFUSED",
  refusals: ["CLEANUP_UNVERIFIED"],
  alerts: [{
    code: "CLEANUP_UNVERIFIED", run_id: longRunId, droplet_id: null, since: "2026-11-24T17:45:00+00:00",
    message: `Cleanup of run ${longRunId} is unverified. New launches are refused until a later sweep verifies its Droplets are gone.`,
  }],
  inventory: { complete: false, checked_at: "2026-11-24T18:29:00+00:00" },
  budget_usd: { total: 10, remaining: 9.3, worst_case_per_run: 0.252 },
  runs: [
    { run_id: longRunId, state: "cleanup_unverified", created_at: "2026-11-24T13:00:00+00:00", deadline: "2026-11-24T17:00:00+00:00", teardown_reason: "deadline", cost_usd: 0.252, reserved_usd: 0.252, settled_usd: null },
    { run_id: "r1", state: "torn_down", created_at: "2026-11-23T10:00:00+00:00", deadline: "2026-11-23T14:00:00+00:00", teardown_reason: "succeeded", cost_usd: 0.0505, reserved_usd: 0.252, settled_usd: 0.0505 },
  ],
};
const quiet = { ...report, launches: "ALLOWED", refusals: [], alerts: [], inventory: null, runs: [] };

async function mockCloud(page: Page, answer: () => { status?: number; body: unknown }) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/bootstrap") return route.fulfill({ json: { operator_token: "mock", config: { workbench: false, models: {}, model_capabilities: {}, budgets: {} }, workbench: false, view_only: false, paid_credentials: {} } });
    if (path === "/api/cloud/status") { const { status = 200, body } = answer(); return route.fulfill({ status, json: body }); }
    if (["/api/episodes", "/api/panels", "/api/batches"].includes(path)) return route.fulfill({ json: [] });
    if (path === "/api/operator/status") return route.fulfill({ json: { running: false, active_episode: null, episodes: [], error: null } });
    return route.fulfill({ json: {} });
  });
}

test("phone cloud screen shows alerts first, absolute times and no sideways scroll", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockCloud(page, () => ({ body: report }));
  await page.goto("/");
  await page.getByRole("navigation").getByRole("button", { name: "Cloud runs", exact: true }).click();
  await expect(page).toHaveURL(/#cloud$/);
  await expect(page.getByRole("heading", { name: "Cloud runs", level: 1 })).toBeVisible();
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("Cleanup of run");
  await expect(alert).toContainText(/17:45:00 UTC/);
  const alertBox = await alert.boundingBox();
  const budgetBox = await page.getByRole("heading", { name: "Budget" }).boundingBox();
  expect(alertBox!.y).toBeLessThan(budgetBox!.y);
  await expect(page.getByRole("region", { name: "Launches" })).toContainText("Launches are refused");
  await expect(page.getByRole("region", { name: "Launches" })).toContainText("CLEANUP_UNVERIFIED");
  const budget = page.getByRole("region", { name: "Budget" });
  await expect(budget).toContainText("$10.0000");
  await expect(budget).toContainText("$9.3000");
  await expect(budget).toContainText("$0.2520");
  await expect(page.getByRole("region", { name: "Inventory" })).toContainText(/incomplete.*18:29:00 UTC|18:29:00 UTC.*incomplete/);
  const finished = page.getByRole("row", { name: /^r1 / });
  await expect(finished).toContainText("torn_down");
  await expect(finished).toContainText("succeeded");
  await expect(finished).toContainText("$0.0505");
  await expect(finished).toContainText(/23 Nov 2026, 14:00:00 UTC/);
  await expect(page.getByText(/Read by the server at 24 Nov 2026, 18:30:05 UTC/)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("nothing recorded is said plainly while launches and the budget still show", async ({ page }) => {
  await mockCloud(page, () => ({ body: quiet }));
  await page.goto("/#cloud");
  await expect(page.getByText("No cloud runs are recorded yet.")).toBeVisible();
  await expect(page.getByRole("region", { name: "Launches" })).toContainText("Launches are allowed");
  await expect(page.getByRole("region", { name: "Budget" })).toContainText("$9.3000");
  await expect(page.getByText("The inventory has not been read yet.")).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

for (const code of ["CLOUD_LEDGER_UNREADABLE", "CLOUD_SETTINGS_INVALID"]) {
  test(`${code} is an error with its code, never an empty list`, async ({ page }) => {
    await mockCloud(page, () => ({ status: 503, body: { error: code } }));
    await page.goto("/#cloud");
    await expect(page.getByRole("alert")).toContainText(code);
    await expect(page.getByText("No cloud runs are recorded yet.")).toHaveCount(0);
    await expect(page.getByRole("region", { name: "Launches" })).toHaveCount(0);
  });
}

test("the screen keeps refreshing and shows when the server last read the records", async ({ page }) => {
  let reads = 0;
  await mockCloud(page, () => {
    reads += 1;
    return { body: reads === 1 ? report : { ...report, alerts: [], read_at: "2026-11-24T18:30:10+00:00", runs: [] } };
  });
  await page.goto("/#cloud");
  await expect(page.getByText(/Read by the server at 24 Nov 2026, 18:30:05 UTC/)).toBeVisible();
  await expect(page.getByText(/Read by the server at 24 Nov 2026, 18:30:10 UTC/)).toBeVisible({ timeout: 15000 });
  await expect(page.getByText("No cloud runs are recorded yet.")).toBeVisible();
  expect(reads).toBeGreaterThanOrEqual(2);
});
