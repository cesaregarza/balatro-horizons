import { test, expect } from "@playwright/test";
import { awaitIdleWorker } from "./runHelpers";

const PRESETS = [
  "Claude Fable 5.2 · Anthropic", "Claude Fable 5.1 · Anthropic", "Claude Opus 5.5 · Anthropic",
  "Claude Sonnet 5.5 · Anthropic", "Claude Haiku 5.5 · Anthropic",
  "GPT-6.2 Astra · OpenAI", "GPT-6.2 Sol · OpenAI", "GPT-6.1 Terra · OpenAI",
  "GPT-6.1 Sol · OpenAI", "GPT-6 Sol · OpenAI", "GPT-6 Luna · OpenAI",
];

test("one preset list offers every model, and a saved GPT-6.2 Sol preset is launchable", async ({ page }) => {
  const bootstrap = await (await page.request.get("/api/bootstrap")).json();
  await awaitIdleWorker(page, bootstrap.operator_token);
  const headers = { "X-BH-Operator": bootstrap.operator_token };
  const original = bootstrap.config;
  const launches: unknown[] = [];
  await page.route("**/api/runs", async (route) => {
    launches.push(route.request().postDataJSON());
    await route.fulfill({ status: 409, json: { detail: "UNEXPECTED_RUN" } });
  });
  try {
    await page.goto("/");
    await page.getByRole("button", { name: "Models & budgets", exact: true }).click();
    const preset = page.getByLabel("Preset", { exact: true });
    await expect(preset.locator("option:not([disabled])")).toHaveText(PRESETS);
    await expect(page.getByText(/Claude Haiku presets cover inputs up to 100k tokens/)).toBeVisible();
    await expect(page.getByText(/OpenAI presets cover inputs up to 272K tokens/)).toBeVisible();
    await preset.selectOption("GPT-6.2 Sol");
    await expect(page.getByLabel("Provider", { exact: true })).toHaveValue("openai");
    await expect(page.getByLabel("Exact model identifier")).toHaveValue("gpt-6.2-sol");
    await expect(page.getByLabel("Input $ / million tokens", { exact: true })).toHaveValue("2");
    await expect(page.getByLabel("Output $ / million tokens")).toHaveValue("10");
    await expect(page.getByLabel("Cache read $ / million tokens")).toHaveValue("0.1");
    await expect(page.getByLabel("Cache write $ / million tokens")).toHaveValue("2.5");
    await expect(page.getByLabel("Price verified on")).toHaveValue("2026-11-22");
    await expect(page.getByLabel("Additional model settings (JSON)")).toHaveValue('{"reasoning_effort":"medium"}');
    await page.getByRole("button", { name: "Save model", exact: true }).click();
    await expect(page.getByText("Model configuration saved.", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "Runs", exact: true }).click();
    const picker = page.getByLabel("Model", { exact: true });
    await picker.selectOption("model:openai:gpt-6.2-sol");
    await expect(picker.locator("option:checked")).toHaveText("GPT-6.2 Sol · OpenAI");
    const effort = page.getByLabel("Reasoning effort", { exact: true });
    await expect(effort).toHaveValue("medium");
    await expect(effort.locator("option")).toHaveText(["Low", "Medium", "High", "Extra high", "Max"]);
    await expect(page.getByText(/Configure a supported model/)).toHaveCount(0);
    await expect(page.getByRole("button", { name: /Start test episode/ })).toBeEnabled();
    expect(launches).toHaveLength(0);
  } finally {
    const response = await page.request.put("/api/settings", {
      headers, data: { models: original.models, budgets: original.budgets, skills: original.skills },
    });
    expect(response.ok()).toBe(true);
  }
});
