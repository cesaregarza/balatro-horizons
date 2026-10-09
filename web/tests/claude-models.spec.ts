import { test, expect, type Page } from "@playwright/test";

const savedKey = "model:openai:gpt-6-luna";
const claudeKey = "model:anthropic:claude-sonnet-5-5";
const preset = {
  provider: "anthropic", model: "claude-sonnet-5-5", input_usd_per_million: 2,
  output_usd_per_million: 10, cached_input_usd_per_million: 0.1,
  cache_write_input_usd_per_million: 2.5, pricing_date: "2026-10-08",
  settings: { reasoning_effort: "high" },
};
const savedModel = {
  provider: "openai", model: "gpt-6-luna", input_usd_per_million: 0.1,
  output_usd_per_million: 0.5, cached_input_usd_per_million: 0.01,
  cache_write_input_usd_per_million: 0.125, pricing_date: "2026-09-22",
  settings: { reasoning_effort: "max" },
};

async function mockApp(page: Page, options: { savedClaude?: boolean; rejectSave?: boolean; unpinned?: boolean } = {}) {
  const settings: any[] = [], starts: any[] = [];
  let config: any = {
    workbench: true, skills: "none", deck: "RED", stake: "GOLD",
    budgets: { paid_calls_enabled: true, max_episode_cost_usd: 5, max_batch_cost_usd: 10 },
    models: { [savedKey]: savedModel, ...(options.savedClaude ? { [claudeKey]: { ...preset, settings: options.unpinned ? {} : preset.settings } } : {}) },
    model_presets: { "Claude Sonnet 5.5": preset },
    model_capabilities: {
      providers: { openai: { supported_settings: ["reasoning_effort", "reasoning_summary", "temperature"] }, anthropic: { supported_settings: ["reasoning_effort", "thinking_budget", "temperature"] } },
      models: {
        [savedKey]: { display_name: "GPT-6 Luna", prompt_cache_diagnostics: true, explicit_cache_mode: true, supported_settings: ["reasoning_effort"], unsupported_settings: {}, reasoning_efforts: ["low", "medium", "high", "xhigh", "max"] },
        [claudeKey]: { display_name: "Claude Sonnet 5.5", prompt_cache_diagnostics: true, explicit_cache_mode: true, supported_settings: ["reasoning_effort"], unsupported_settings: {}, reasoning_efforts: ["low", "medium", "high", "xhigh", "max"], default_reasoning_effort: "high" },
      },
    },
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (path === "/api/bootstrap") return route.fulfill({ json: { operator_token: "mock", config, workbench: true, paid_credentials: { openai: true, anthropic: false } } });
    if (["/api/episodes", "/api/panels", "/api/batches"].includes(path)) return route.fulfill({ json: [] });
    if (path === "/api/operator/status") return route.fulfill({ json: { running: false, active_episode: null, episodes: [], error: null } });
    if (path === "/api/settings" && request.method() === "PUT") {
      settings.push(request.postDataJSON());
      if (options.rejectSave) return route.fulfill({ status: 400, json: { detail: "CLAUDE_SETTINGS_REFUSED" } });
      config = { ...config, ...request.postDataJSON() };
      return route.fulfill({ json: config });
    }
    if (path === "/api/runs" && request.method() === "POST") {
      starts.push(request.postDataJSON());
      return route.fulfill({ json: { episode_id: "claude-test-episode-000001" } });
    }
    return route.fulfill({ json: {} });
  });
  await page.goto("/");
  return { settings, starts };
}

test("Claude preset is opt-in and explicit save preserves existing models and caps", async ({ page }) => {
  const { settings, starts } = await mockApp(page);
  await page.getByRole("button", { name: "Models & budgets", exact: true }).click();
  await page.getByLabel("Claude preset").selectOption("Claude Sonnet 5.5");
  await expect(page.getByLabel("Provider", { exact: true })).toHaveValue("anthropic");
  await expect(page.getByLabel("Exact model identifier")).toHaveValue("claude-sonnet-5-5");
  await expect(page.getByLabel("Input $ / million tokens", { exact: true })).toHaveValue("2");
  await expect(page.getByLabel("Output $ / million tokens")).toHaveValue("10");
  await expect(page.getByLabel("Cache read $ / million tokens")).toHaveValue("0.1");
  await expect(page.getByLabel("Cache write $ / million tokens")).toHaveValue("2.5");
  await expect(page.getByLabel("Price verified on")).toHaveValue("2026-10-08");
  await expect(page.getByLabel("Additional model settings (JSON)")).toHaveValue('{"reasoning_effort":"high"}');
  await expect(page.getByText("Credential has not been configured.", { exact: true })).toBeVisible();
  expect(settings).toHaveLength(0);
  expect(starts).toHaveLength(0);
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.getByText("Model configuration saved.", { exact: true })).toBeVisible();
  expect(settings).toEqual([{
    models: { [savedKey]: savedModel, [claudeKey]: preset },
    budgets: { paid_calls_enabled: true, max_episode_cost_usd: 5, max_batch_cost_usd: 10 }, skills: "none",
  }]);
  expect(starts).toHaveLength(0);
});

test("Claude launch sends selected Max effort without overwriting defaults", async ({ page }) => {
  const { starts, settings } = await mockApp(page, { savedClaude: true });
  await page.getByLabel("Model", { exact: true }).selectOption(claudeKey);
  const effort = page.getByLabel("Reasoning effort");
  await expect(effort).toHaveValue("high");
  await expect.poll(() => effort.locator("option").evaluateAll((nodes) => nodes.map((node) => (node as HTMLOptionElement).value))).toEqual(["low", "medium", "high", "xhigh", "max"]);
  await effort.selectOption("max");
  await page.getByRole("button", { name: /Start test episode/ }).click();
  await expect.poll(() => starts.length).toBe(1);
  expect(starts[0]).toMatchObject({ agent: claudeKey, offline: true, model_settings: { reasoning_effort: "max" } });
  expect(settings).toHaveLength(0);
});

test("manually configured Claude uses its declared default effort", async ({ page }) => {
  await mockApp(page, { savedClaude: true, unpinned: true });
  await page.getByLabel("Model", { exact: true }).selectOption(claudeKey);
  await expect(page.getByLabel("Reasoning effort")).toHaveValue("high");
});

test("Claude cache pricing must be paired and can be explicitly cleared", async ({ page }) => {
  const { settings } = await mockApp(page, { savedClaude: true });
  await page.getByRole("button", { name: "Models & budgets", exact: true }).click();
  await page.getByLabel("Claude preset").selectOption("Claude Sonnet 5.5");
  await page.getByLabel("Cache read $ / million tokens").fill("");
  await expect(page.getByRole("button", { name: "Save model", exact: true })).toBeDisabled();
  await page.getByLabel("Cache write $ / million tokens").fill("");
  await page.getByLabel("Additional model settings (JSON)").fill("{}");
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect.poll(() => settings.length).toBe(1);
  expect(settings[0].models[claudeKey]).toEqual({ ...preset, cached_input_usd_per_million: null, cache_write_input_usd_per_million: null, settings: {} });
});

test("invalid settings JSON and server refusal surface without losing the form", async ({ page }) => {
  const { settings } = await mockApp(page, { rejectSave: true });
  await page.getByRole("button", { name: "Models & budgets", exact: true }).click();
  await page.getByLabel("Claude preset").selectOption("Claude Sonnet 5.5");
  await page.getByLabel("Additional model settings (JSON)").fill("[]");
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Model settings must be a JSON object.");
  expect(settings).toHaveLength(0);
  await page.getByLabel("Additional model settings (JSON)").fill('{"reasoning_effort":"max"}');
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("CLAUDE_SETTINGS_REFUSED");
  await expect(page.getByLabel("Exact model identifier")).toHaveValue("claude-sonnet-5-5");
});
