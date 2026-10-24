import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { test, expect } from "@playwright/test";
import { awaitIdleWorker } from "./runHelpers";

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
    if (path === "/api/operator/episodes") { enrichedCalls++; return route.fulfill({ json: enriched.map((row, index) => ({ ...row, context_policy: index === 0 ? "append_only_decision_v1" : null, provider_wire_policy: index === 0 ? "anthropic_messages_v1" : null })) }); }
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
  await expect(page.getByRole("row", { name: /Context policy/ })).toContainText("append_only_decision_v1");
  await expect(page.getByRole("row", { name: /Provider wire policy/ })).toContainText("anthropic_messages_v1");
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

test("a run created after Operator details was chosen is not exposed without an explicit action", async ({ page }) => {
  let enrichedCalls = 0;
  let current = rows;
  const fresh = { episode_id: "d".repeat(32), created_at: "2026-09-23T12:00:00Z", evidence_kind: "NATIVE", deck: "Red", stake: "Gold", branch: false, evaluation_eligible: true, fixture: null };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/bootstrap") return route.fulfill({ json: { operator_token: "mock", config: { workbench: true, models: {}, model_capabilities: {}, budgets: {} }, workbench: true, paid_credentials: {} } });
    if (path === "/api/episodes") return route.fulfill({ json: current });
    if (path === "/api/operator/episodes") {
      enrichedCalls++;
      return route.fulfill({ json: [...enriched, ...(current.includes(fresh) ? [{ ...fresh, parent_episode_id: null, model_name: "Fresh Model", reasoning_effort: null, recorded_interface: "harness-v1", outcome: null, reason: null }] : [])] });
    }
    if (["/api/panels", "/api/batches"].includes(path)) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Operator details" }).click();
  await expect(page.getByRole("heading", { name: "Luna Max", exact: true })).toBeVisible();
  expect(enrichedCalls).toBe(1);
  // A new run appears in the list while Operator details is still selected.
  current = [...rows, fresh];
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(page.getByRole("button", { name: "Blinded review" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("Fresh Model")).toHaveCount(0);
  expect(enrichedCalls).toBe(1);
  // Choosing Operator details again is the explicit action that reveals the new run.
  await page.getByRole("button", { name: "Operator details" }).click();
  await expect(page.getByRole("heading", { name: "Fresh Model", exact: true })).toBeVisible();
  expect(enrichedCalls).toBe(2);
});

test("operator details ask only for the runs listed when the operator chose them", async ({ page }) => {
  const requests: { method: string; body: unknown }[] = [];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/bootstrap") return route.fulfill({ json: { operator_token: "mock", config: { workbench: true, models: {}, model_capabilities: {}, budgets: {} }, workbench: true, paid_credentials: {} } });
    if (path === "/api/episodes") return route.fulfill({ json: rows });
    if (path === "/api/operator/episodes") { requests.push({ method: route.request().method(), body: route.request().postDataJSON() }); return route.fulfill({ json: enriched }); }
    if (["/api/panels", "/api/batches"].includes(path)) return route.fulfill({ json: [] });
    if (path === "/api/operator/status") return route.fulfill({ json: { running: false, active_episode: null, episodes: [], error: null } });
    return route.fulfill({ json: {} });
  });
  await page.goto("/");
  await expect(page.locator(".episode-card")).toHaveCount(3);
  await page.getByRole("button", { name: "Operator details" }).click();
  await expect(page.getByRole("heading", { name: "Luna Max", exact: true })).toBeVisible();
  expect(requests).toEqual([{ method: "POST", body: { episode_ids: rows.map((row) => row.episode_id) } }]);
});

test("operator details reveal only the runs on screen when a refresh lands mid-click", async ({ page }) => {
  // Two real synthetic runs: the screen shows A; a held refresh then resolves with A and B.
  const { operator_token } = await (await page.request.get("/api/bootstrap")).json();
  const headers = { "X-BH-Operator": operator_token };
  const ids: string[] = [];
  for (let index = 0; index < 2; index++) {
    await awaitIdleWorker(page, operator_token);
    const created = await page.request.post("/api/runs", { headers, data: { agent: "heuristic", offline: true } });
    expect(created.ok(), await created.text()).toBe(true);
    ids.push((await created.json()).episode_id);
  }
  await awaitIdleWorker(page, operator_token);
  const [a, b] = ids;
  let listed = [a];
  await page.route("**/api/episodes", async (route) => {
    const rows = await (await route.fetch()).json();
    await route.fulfill({ json: rows.filter((row: { episode_id: string }) => listed.includes(row.episode_id)) });
  });
  await page.addInitScript(() => {
    const realFetch = window.fetch.bind(window);
    const state = window as any;
    state.pendingFetches = 0;
    window.fetch = (input, init) => {
      const path = new URL(String(input instanceof Request ? input.url : input), location.href).pathname;
      if (state.holdEpisodes && path === "/api/episodes") {
        state.holdEpisodes = false;
        return realFetch(input, init).then(async (response) => {
          const body = await response.json();
          return new Promise((resolve) => { state.releaseEpisodes = () => resolve({ ok: true, json: async () => body }); });
        });
      }
      state.pendingFetches++;
      return realFetch(input, init).finally(() => { state.pendingFetches--; });
    };
  });
  const bodies: unknown[] = [];
  page.on("request", (request) => { if (new URL(request.url()).pathname === "/api/operator/episodes") bodies.push(request.postDataJSON()); });
  await page.goto("/");
  await expect(page.locator(".episode-card")).toHaveCount(1);
  listed = [a, b];
  await page.evaluate(() => { (window as any).holdEpisodes = true; });
  await page.locator("header").getByRole("button", { name: "Refresh", exact: true }).click();
  await expect.poll(() => page.evaluate(() => typeof (window as any).releaseEpisodes === "function" && (window as any).pendingFetches === 0)).toBe(true);
  // Resolve the refresh, let its promise chain hand the list to React, then click before React commits it.
  const shownAtClick = await page.evaluate(async () => {
    (window as any).releaseEpisodes();
    for (let turn = 0; turn < 50; turn++) await Promise.resolve();
    const shown = document.querySelectorAll(".episode-card").length;
    Array.from(document.querySelectorAll("button")).find((button) => button.textContent === "Operator details")!.click();
    return shown;
  });
  expect(shownAtClick).toBe(1);
  const operatorResponse = await page.waitForResponse((response) => new URL(response.url()).pathname === "/api/operator/episodes");
  expect(operatorResponse.ok()).toBe(true);
  expect(bodies).toEqual([{ episode_ids: [a] }]);
  await expect(page.locator(".episode-card")).toHaveCount(2);
  // The real server's exposure ledger: A was revealed, B never was.
  const ledger = (eid: string) => {
    const path = fileURLToPath(new URL(`../.e2e-data-dashboard/review/${eid}-exposure.jsonl`, import.meta.url));
    return existsSync(path) ? readFileSync(path, "utf8").split("\n").filter(Boolean).map((line) => JSON.parse(line)).filter((record) => record.kind === "operator_library") : [];
  };
  expect(ledger(a)).toHaveLength(1);
  expect(ledger(b)).toEqual([]);
});
