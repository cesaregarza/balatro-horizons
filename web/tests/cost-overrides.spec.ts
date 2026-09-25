import { test, expect, type Page } from "@playwright/test";

const episodeId = "a".repeat(32);
const model = {
  provider: "anthropic",
  model: "claude-cost-test",
  input_usd_per_million: 1,
  output_usd_per_million: 2,
  pricing_date: "2026-09-24",
  settings: {},
};
const config = {
  workbench: true,
  models: { "test-model": model },
  model_capabilities: { providers: { openai: { supported_settings: [] }, anthropic: { supported_settings: [] } }, models: {} },
  budgets: { paid_calls_enabled: true, max_episode_cost_usd: 1, max_batch_cost_usd: 2 },
  skills: "none",
};

async function openRunLibrary(page: Page, options: { delayStart?: boolean; runStatus?: number } = {}) {
  const runs: unknown[] = [];
  const settings: unknown[] = [];
  let releaseStart = () => {};
  const startGate = new Promise<void>((resolve) => { releaseStart = resolve; });
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/bootstrap") {
      return route.fulfill({ json: { operator_token: "mock-operator", config, workbench: true, paid_credentials: {} } });
    }
    if (path === "/api/episodes") return route.fulfill({ json: [] });
    if (path === "/api/panels" || path === "/api/batches") return route.fulfill({ json: [] });
    if (path === "/api/operator/status") {
      return route.fulfill({ json: { running: false, active_episode: null, episodes: [], error: null } });
    }
    if (path === "/api/settings" && request.method() === "PUT") {
      const body = request.postDataJSON();
      settings.push(body);
      return route.fulfill({ json: { ...config, ...body } });
    }
    if (path === "/api/runs" && request.method() === "POST") {
      runs.push(request.postDataJSON());
      if (options.delayStart) await startGate;
      if (options.runStatus) return route.fulfill({ status: options.runStatus, json: { error: "START_FAILED" } });
      return route.fulfill({ json: { episode_id: episodeId } });
    }
    return route.fulfill({ status: 200, json: {} });
  });
  await page.goto("/");
  await page.getByLabel("Model", { exact: true }).selectOption("model:anthropic:claude-cost-test");
  await expect(page.getByRole("group", { name: "Cost for this new run" })).toBeVisible();
  return { runs, settings, releaseStart };
}

test("default limits omit both override fields from a new run", async ({ page }) => {
  const { runs, settings } = await openRunLibrary(page);
  await page.getByRole("button", { name: /Start test episode/ }).click();
  await expect(page.getByRole("status")).toContainText("Run created");
  expect(settings).toHaveLength(1);
  expect(settings[0]).toMatchObject({ budgets: config.budgets });
  expect(runs).toEqual([{ agent: "model:anthropic:claude-cost-test", offline: true, preset: "pilot", seed: null }]);
});

test("$10 sends the exact per-run ceiling", async ({ page }) => {
  const { runs } = await openRunLibrary(page);
  await page.getByRole("button", { name: "$10 total" }).click();
  await page.getByRole("button", { name: /Start test episode/ }).click();
  await expect(page.getByRole("status")).toContainText("Run created");
  expect(runs).toEqual([{
    agent: "model:anthropic:claude-cost-test", offline: true, preset: "pilot", seed: null, cost_override: 10,
  }]);
});

test("switching from a configured model to a baseline drops its override", async ({ page }) => {
  const { runs } = await openRunLibrary(page);
  await page.getByRole("button", { name: "$10 total" }).click();
  const modelPicker = page.getByLabel("Model", { exact: true });
  await modelPicker.selectOption("heuristic");
  await expect(page.getByRole("group", { name: "Cost for this new run" })).toHaveCount(0);
  await page.getByRole("button", { name: /Start test episode/ }).click();
  await expect(page.getByRole("status")).toContainText("Run created");
  expect(runs).toEqual([{ agent: "heuristic", offline: true, preset: "pilot", seed: null }]);
});

test("failed start clears duplicate guard and reenables start for a fresh attempt", async ({ page }) => {
  const { runs } = await openRunLibrary(page, { runStatus: 503 });
  const start = page.getByRole("button", { name: /Start test episode/ });
  await start.click();
  await expect(page.getByRole("alert")).toContainText("START_FAILED");
  await expect(start).toBeEnabled();
  await start.click();
  await expect.poll(() => runs.length).toBe(2);
  await expect(start).toBeEnabled();
});

test("Uncapped is red and cancelling confirmation preserves the previous choice", async ({ page }) => {
  const { runs } = await openRunLibrary(page);
  const uncapped = page.getByRole("button", { name: "Uncapped" });
  await expect(uncapped).toHaveCSS("background-color", "rgb(142, 41, 50)");
  await page.getByRole("button", { name: "$10 total" }).click();
  page.once("dialog", (dialog) => {
    expect(dialog.message()).toMatch(/^Are you sure\?/);
    expect(dialog.message()).toContain("no dollar ceiling");
    expect(dialog.message()).toMatch(/spending can keep growing/i);
    expect(dialog.message()).toContain("Call and action limits still apply");
    void dialog.dismiss();
  });
  await uncapped.click();
  await expect(page.getByRole("button", { name: "$10 total" })).toHaveAttribute("aria-pressed", "true");
  await expect(uncapped).toHaveAttribute("aria-pressed", "false");
  expect(runs).toEqual([]);
});

test("accepted Uncapped confirmation sends its required flag", async ({ page }) => {
  const { runs } = await openRunLibrary(page);
  page.once("dialog", (dialog) => { void dialog.accept(); });
  await page.getByRole("button", { name: "Uncapped" }).click();
  await expect(page.getByRole("button", { name: "Uncapped" })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: /Start test episode/ }).click();
  await expect(page.getByRole("status")).toContainText("Run created");
  expect(runs).toEqual([{
    agent: "model:anthropic:claude-cost-test", offline: true, preset: "pilot", seed: null,
    cost_override: "uncapped", confirm_uncapped: true,
  }]);
});

test("successful start resets Uncapped so the next run uses current limits", async ({ page }) => {
  const { runs } = await openRunLibrary(page);
  page.once("dialog", (dialog) => { void dialog.accept(); });
  await page.getByRole("button", { name: "Uncapped" }).click();
  const start = page.getByRole("button", { name: /Start test episode/ });
  await start.click();
  await expect(start).toBeEnabled();
  await expect(page.getByRole("button", { name: "Current limits" })).toHaveAttribute("aria-pressed", "true");
  await start.click();
  await expect.poll(() => runs.length).toBe(2);
  expect(runs[0]).toMatchObject({ cost_override: "uncapped", confirm_uncapped: true });
  expect(runs[1]).toEqual({ agent: "model:anthropic:claude-cost-test", offline: true, preset: "pilot", seed: null });
});

test("busy state disables overrides and rapid duplicate clicks submit once", async ({ page }) => {
  const { runs, releaseStart } = await openRunLibrary(page, { delayStart: true });
  const start = page.getByRole("button", { name: /Start test episode/ });
  await start.click();
  await expect(start).toBeDisabled();
  await expect(page.getByRole("button", { name: "Current limits" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "$10 total" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Uncapped" })).toBeDisabled();
  await start.click({ force: true });
  expect(runs).toHaveLength(1);
  releaseStart();
  await expect(start).toBeEnabled();
  expect(runs).toHaveLength(1);
});
