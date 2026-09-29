import { test, expect } from "@playwright/test";

const rows = [
  { episode_id: "a".repeat(32), created_at: "2026-09-20T12:00:00Z", evidence_kind: "NATIVE", deck: "Red", stake: "Gold", branch: false, evaluation_eligible: true, fixture: null, agent: "model:test:first" },
  { episode_id: "b".repeat(32), created_at: "2026-09-21T12:00:00Z", evidence_kind: "NATIVE", deck: "Blue", stake: "White", branch: false, evaluation_eligible: true, fixture: null, agent: "model:test:second" },
  { episode_id: "c".repeat(32), created_at: "2026-09-22T12:00:00Z", evidence_kind: "NATIVE", deck: "Red", stake: "White", branch: false, evaluation_eligible: false, fixture: "test_setup", agent: "heuristic" },
];
const enriched = rows.map((row, index) => ({ ...row, parent_episode_id: null, model_name: ["Luna Max", "Sol", "Heuristic"][index], reasoning_effort: ["high", "low", null][index], recorded_interface: "harness-v1", outcome: index === 1 ? "WIN" : null, reason: null, cost_usd: index === 1 ? 0.42 : null, committed_actions: index === 1 ? 52 : null }));

test("blinded library avoids enriched fetch; operator view searches and compares exactly two records", async ({ page }) => {
  let enrichedCalls = 0;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/bootstrap") return route.fulfill({ json: { operator_token: "mock", config: { workbench: true, models: {}, model_capabilities: {}, budgets: {} }, workbench: true, paid_credentials: {} } });
    if (path === "/api/episodes") return route.fulfill({ json: rows });
    if (path === "/api/operator/episodes") { enrichedCalls++; return route.fulfill({ json: enriched }); }
    if (["/api/panels", "/api/batches"].includes(path)) return route.fulfill({ json: [] });
    if (path === "/api/operator/status") return route.fulfill({ json: { running: false, active_episode: null, episodes: [], error: null } });
    return route.fulfill({ json: {} });
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Run library" })).toBeVisible();
  expect(enrichedCalls).toBe(0);
  await page.getByLabel("Evidence filter").selectOption("EVALUATOR_FIXTURE");
  await expect(page.locator(".episode-card")).toHaveCount(1);
  await expect(page.locator(".episode-card")).toContainText("evaluator fixture");
  await page.getByLabel("Evidence filter").selectOption("all");
  await page.getByRole("button", { name: "Operator details" }).click();
  await expect(page.getByRole("heading", { name: "Luna Max", exact: true })).toBeVisible();
  expect(enrichedCalls).toBe(1);
  await page.getByLabel("Search runs").fill("Luna Max");
  await expect(page.locator(".episode-card")).toHaveCount(1);
  await page.getByLabel("Search runs").clear();
  await page.getByLabel("Model filter").selectOption("Sol");
  await expect(page.locator(".episode-card")).toHaveCount(1);
  await page.getByLabel("Model filter").selectOption("all");
  await page.getByLabel("Select aaaaaaaaaa for comparison").check();
  await page.getByLabel("Select bbbbbbbbbb for comparison").check();
  await expect(page.getByRole("heading", { name: "Descriptive comparison" })).toBeVisible();
  await expect(page.getByText(/continuations may share earlier decisions/)).toBeVisible();
  await expect(page.getByRole("cell", { name: "$0.4200" })).toBeVisible();
});

test("phone library keeps Explore decisions visible on compact run cards", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/bootstrap") return route.fulfill({ json: { operator_token: "mock", config: { workbench: false, models: {}, model_capabilities: {}, budgets: {} }, workbench: false, paid_credentials: {} } });
    if (path === "/api/episodes") return route.fulfill({ json: rows });
    if (["/api/panels", "/api/batches"].includes(path)) return route.fulfill({ json: [] });
    if (path === "/api/operator/status") return route.fulfill({ json: { running: false, active_episode: null, episodes: [], error: null } });
    return route.fulfill({ json: {} });
  });
  await page.goto("/");
  const explore = page.getByRole("button", { name: "Explore decisions" }).first();
  await expect(explore).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("continuations stay grouped across pages and retain their own actions", async ({ page }) => {
  const parentId = "a".repeat(32);
  const childId = "b".repeat(32);
  const parent = { ...enriched[0], episode_id: parentId };
  const child = { ...enriched[1], episode_id: childId, parent_episode_id: parentId, branch: true };
  const fillers = Array.from({ length: 15 }, (_, index) => ({ ...enriched[0], episode_id: (index + 1).toString(16).padStart(32, "0"), created_at: "2026-09-25T12:00:00Z" }));
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/bootstrap") return route.fulfill({ json: { operator_token: "mock", config: { workbench: true, models: {}, model_capabilities: {}, budgets: {} }, workbench: true, paid_credentials: {} } });
    if (path === "/api/episodes") return route.fulfill({ json: [] });
    if (path === "/api/operator/episodes") return route.fulfill({ json: [parent, child, ...fillers] });
    if (path === "/api/panels" || path === "/api/batches") return route.fulfill({ json: [] });
    if (path === "/api/operator/status") return route.fulfill({ json: { running: false, active_episode: null, episodes: [], error: null } });
    if (path === "/api/explore/sessions") return route.fulfill({ json: { review_token: "mock-session", view: null } });
    return route.fulfill({ json: {} });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Operator details" }).click();
  await expect(page.locator(".episode-card")).toHaveCount(15);
  await page.getByRole("navigation", { name: "Run pages" }).getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.locator(".episode-group .episode-card")).toHaveCount(2);
  const childCard = page.locator(".episode-card").filter({ hasText: childId.slice(0, 12) });
  await expect(childCard.getByRole("button", { name: "Compare outcomes" })).toBeVisible();
  const opened = page.waitForRequest((request) => new URL(request.url()).pathname === "/api/explore/sessions" && request.method() === "POST");
  await childCard.getByRole("button", { name: "Explore decisions" }).click();
  expect((await opened).postDataJSON()).toMatchObject({ episode_id: childId, retrospective: true });
});
