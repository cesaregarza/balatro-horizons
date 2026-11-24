import { expect, test } from "@playwright/test";
import { explorerEpisode, mockExplorer } from "./explorerFixture";

const bootstrap = { operator_token: "mock", config: { workbench: false, models: {}, model_capabilities: {}, budgets: {} }, workbench: false, view_only: true, paid_credentials: {} };
const batch = { batch_id: "b".repeat(32), slots: [], config: { deck: "RED", stake: "GOLD" }, split: "exploratory" };

test("view-only mode hides every control whose route is absent", async ({ page }) => {
  const writes: string[] = [];
  page.on("request", (request) => { if (request.method() !== "GET") writes.push(`${request.method()} ${new URL(request.url()).pathname}`); });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/bootstrap") return route.fulfill({ json: bootstrap });
    if (path === "/api/batches") return route.fulfill({ json: [batch] });
    if (["/api/episodes", "/api/panels"].includes(path)) return route.fulfill({ json: [] });
    if (path === "/api/operator/status") return route.fulfill({ json: { running: false, active_episode: null, episodes: [], error: null } });
    return route.fulfill({ json: {} });
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Runs and experiments" })).toBeVisible();
  await expect(page.getByText("View only", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /Start a run/ })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Start a run" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Stop worker/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Save model defaults/ })).toHaveCount(0);
  const navigation = page.getByRole("navigation");
  await expect(navigation.getByRole("button", { name: "Cloud runs", exact: true })).toBeVisible();
  await expect(navigation.getByRole("button", { name: "Models & budgets" })).toHaveCount(0);
  await navigation.getByRole("button", { name: "Batches & reports" }).click();
  await expect(page.getByRole("button", { name: "Reveal report" })).toBeVisible();
  for (const name of [/Freeze plan/, /Generate 20 private development seeds/, /Export public results/, /^Run synthetic test/]) {
    await expect(page.getByRole("button", { name })).toHaveCount(0);
  }
  await page.evaluate(() => { window.location.hash = "#models"; });
  await expect(page.getByRole("alert")).toContainText("view-only");
  await expect(page.getByRole("heading", { name: "Models & budgets" })).toHaveCount(0);
  expect(writes).toEqual([]);
});

test("view-only mode offers no way to annotate a decision", async ({ page }) => {
  await mockExplorer(page);
  await page.route("**/api/bootstrap", (route) => route.fulfill({ json: bootstrap }));
  await page.goto(`/#explore/${explorerEpisode}/20`);
  await expect(page.getByRole("button", { name: "Before decision" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Annotate this decision" })).toHaveCount(0);
});
