import { test, expect } from "@playwright/test";

const modelKey = "model:openai:gpt-5.6-luna";
const config = {
  workbench: true,
  models: { [modelKey]: { provider: "openai", model: "gpt-5.6-luna", input_usd_per_million: 1, output_usd_per_million: 2, cached_input_usd_per_million: 0.1, cache_write_input_usd_per_million: 1.2, pricing_date: "2026-09-24", settings: { temperature: 0.2 } } },
  model_capabilities: { providers: { openai: { supported_settings: ["temperature", "reasoning_effort"] }, anthropic: { supported_settings: [] } }, models: { [modelKey]: { display_name: "GPT-5.6 Luna", prompt_cache_diagnostics: true, explicit_cache_mode: true, supported_settings: ["temperature", "reasoning_effort"], unsupported_settings: {}, reasoning_efforts: ["low", "medium", "high"] } } },
  budgets: { paid_calls_enabled: true, max_episode_cost_usd: 3.5, max_batch_cost_usd: 12 }, skills: "none",
};

async function mockApp(page: import("@playwright/test").Page) {
  const starts: any[] = [];
  const settings: any[] = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/bootstrap") return route.fulfill({ json: { operator_token: "mock", config, workbench: true, paid_credentials: {} } });
    if (path === "/api/episodes") return route.fulfill({ json: [] });
    if (path === "/api/panels" || path === "/api/batches") return route.fulfill({ json: [] });
    if (path === "/api/explore/sessions") return route.fulfill({ json: { review_token: "mock-review", view: null } });
    if (path === "/api/explore/decisions") return route.fulfill({ json: { manifest: { episode_id: "a".repeat(32), agent: "fixture", config: { models: {} } }, summary: null, actions: [], uncommitted_actions: [], rounds: [] } });
    if (path === "/api/operator/status") return route.fulfill({ json: { running: false, active_episode: null, episodes: [], error: null } });
    if (path === "/api/settings" && request.method() === "PUT") { settings.push(request.postDataJSON()); return route.fulfill({ json: config }); }
    if (path === "/api/runs" && request.method() === "POST") { starts.push(request.postDataJSON()); return route.fulfill({ json: { episode_id: "launch-episode-00000001" } }); }
    if (path === "/api/operator/episodes/launch-episode-00000001/explore" || path.endsWith("/explorer")) return route.fulfill({ json: {} });
    return route.fulfill({ json: {} });
  });
  await page.goto("/");
  return { starts, settings };
}

test("launch shows effective limits and sends selected settings without saving defaults", async ({ page }) => {
  const { starts, settings } = await mockApp(page);
  await page.getByLabel("Model", { exact: true }).selectOption(modelKey);
  await expect(page.getByLabel("Launch summary")).toContainText("Synthetic test");
  await expect(page.getByLabel("Launch summary")).toContainText("$3.50");
  await expect(page.getByLabel("Launch summary")).toContainText("$12.00");
  await page.getByLabel("Reasoning effort").selectOption("high");
  await page.getByRole("button", { name: /Start test episode/ }).click();
  await expect.poll(() => starts.length).toBe(1);
  expect(starts[0]).toMatchObject({ agent: modelKey, model_settings: { temperature: 0.2, reasoning_effort: "high" }, offline: true, preset: "pilot", seed: null });
  expect(settings).toHaveLength(0);
});

test("launch draft, including private seed, survives tab navigation in memory", async ({ page }) => {
  await mockApp(page);
  await page.getByLabel("Private seed").fill("private-draft-seed");
  await page.getByLabel("Game configuration").selectOption("smoke");
  await page.getByRole("button", { name: "Models & budgets", exact: true }).click();
  await page.getByRole("button", { name: "Runs", exact: true }).click();
  await expect(page.getByLabel("Private seed")).toHaveValue("private-draft-seed");
  await expect(page.getByLabel("Game configuration")).toHaveValue("smoke");
  expect(await page.evaluate(() => localStorage.length)).toBe(0);
});
