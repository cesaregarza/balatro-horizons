import type { ChildProcess } from "node:child_process";
import { EventEmitter } from "node:events";
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { PassThrough } from "node:stream";
import { test, expect } from "@playwright/test";
import {
  configureModel,
  effortOptions,
  harnessLabel,
  modelLabel,
  modelCatalog,
  modelKey,
  type CapabilityTable,
  type ModelConfig,
} from "../src/modelSelection";
import { awaitIdleWorker } from "./runHelpers";
import { startLegacyServer, type LegacyServer } from "./legacyServer";

const terra: ModelConfig = {
  provider: "openai",
  model: "gpt-5.6-terra",
  input_usd_per_million: 2,
  output_usd_per_million: 12,
  cached_input_usd_per_million: 0.2,
  cache_write_input_usd_per_million: 2.5,
  pricing_date: "2026-09-15",
  settings: {
    reasoning_effort: "medium",
    reasoning_summary: "auto",
  },
};
const luna: ModelConfig = {
  provider: "openai",
  model: "gpt-5.6-luna",
  input_usd_per_million: 0.2,
  output_usd_per_million: 1.2,
  pricing_date: "2026-09-15",
  settings: { reasoning_effort: "medium" },
};

const sol: ModelConfig = {
  ...terra,
  model: "gpt-5.6-sol",
  input_usd_per_million: 4,
  output_usd_per_million: 20,
  cached_input_usd_per_million: 0.4,
  cache_write_input_usd_per_million: 5,
};
const astra: ModelConfig = {
  ...terra,
  model: "gpt-6-astra",
  input_usd_per_million: 10,
  output_usd_per_million: 50,
  cached_input_usd_per_million: 1,
  cache_write_input_usd_per_million: 12.5,
};

const capabilities: CapabilityTable = {
  providers: {
    openai: {
      supported_settings: ["temperature", "reasoning_effort", "reasoning_summary"],
    },
    anthropic: { supported_settings: ["temperature", "thinking_budget"] },
  },
  models: {
    [modelKey(terra)]: {
      display_name: "GPT-5.6 Terra",
      prompt_cache_diagnostics: true,
      explicit_cache_mode: true,
      supported_settings: ["temperature", "reasoning_effort", "reasoning_summary"],
      unsupported_settings: {},
      reasoning_efforts: ["none", "low", "medium", "high", "xhigh", "max"],
    },
    [modelKey(sol)]: {
      display_name: "GPT-5.6 Sol",
      prompt_cache_diagnostics: true,
      explicit_cache_mode: true,
      supported_settings: ["temperature", "reasoning_effort", "reasoning_summary"],
      unsupported_settings: {},
      reasoning_efforts: ["none", "low", "medium", "high", "xhigh", "max"],
    },
    [modelKey(astra)]: {
      display_name: "GPT-6 Astra",
      prompt_cache_diagnostics: true,
      explicit_cache_mode: true,
      supported_settings: ["temperature", "reasoning_effort", "reasoning_summary"],
      unsupported_settings: {},
      reasoning_efforts: ["low", "medium", "high", "xhigh", "max"],
    },
    [modelKey(luna)]: {
      display_name: "GPT-5.6 Luna",
      prompt_cache_diagnostics: true,
      explicit_cache_mode: true,
      supported_settings: ["temperature", "reasoning_effort", "reasoning_summary"],
      unsupported_settings: { reasoning_effort: ["minimal"] },
      reasoning_efforts: ["none", "low", "medium", "high", "xhigh", "max"],
    },
  },
};

test("Sol and Astra expose their supported reasoning choices", () => {
  expect(modelLabel(sol, capabilities)).toBe("GPT-5.6 Sol · OpenAI");
  expect(modelLabel(astra, capabilities)).toBe("GPT-6 Astra · OpenAI");
  expect(effortOptions(sol, capabilities)).toContain("none");
  expect(effortOptions(astra, capabilities)).toEqual([
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
  ]);
  expect(() => configureModel(astra, "none", capabilities)).toThrow();
  expect(configureModel(astra, "max", capabilities).settings.reasoning_effort).toBe("max");
});

test("declared unsupported settings are enforced without model-name rules", () => {
  const guarded: CapabilityTable = {
    ...capabilities,
    models: {
      ...capabilities.models,
      [modelKey(terra)]: {
        ...capabilities.models[modelKey(terra)],
        unsupported_settings: { reasoning_summary: ["auto"] },
      },
    },
  };
  expect(() => configureModel(terra, "medium", guarded)).toThrow(
    "Unsupported reasoning_summary setting for this model.",
  );
});

test("model defaults deduplicate aliases without rewriting historical settings", () => {
  const legacy = { ...terra, settings: { reasoning_effort: "medium" } };
  const savedAlias = { ...terra, settings: { ...terra.settings } };
  const currentAlias = configureModel(terra, "medium", capabilities);
  const models = {
    "terra-current": currentAlias,
    "terra-saved": savedAlias,
    "terra-tools": legacy,
    "luna-tools": luna,
  };
  expect(Object.keys(modelCatalog(models))).toHaveLength(2);
  expect(modelCatalog(models)[modelKey(terra)]).toEqual(currentAlias);
  expect(
    modelCatalog({ "terra-saved": savedAlias, "terra-tools": legacy })[
      modelKey(terra)
    ],
  ).toEqual(savedAlias);
  expect(harnessLabel()).toBe("Current harness");
  expect(harnessLabel("recorded-current", true)).toBe("Current harness");
  expect(harnessLabel("retired-recording", false)).toBe(
    "Legacy harness (retired-recording)",
  );
  const selected = configureModel(terra, "high", capabilities);
  expect(
    modelCatalog({ ...models, [modelKey(terra)]: selected })[modelKey(terra)],
  ).toEqual(selected);
  expect(terra.settings.reasoning_effort).toBe("medium");
  expect(selected.cached_input_usd_per_million).toBe(0.2);
  expect(() => configureModel(terra, "minimal", capabilities)).toThrow();
  expect(() => configureModel(luna, "medium", capabilities)).toThrow();
  const anthropic: ModelConfig = {
    ...luna,
    provider: "anthropic",
    model: "pinned-anthropic",
    settings: { thinking_budget: 2048 },
  };
  const anthropicCapabilities: CapabilityTable = {
    ...capabilities,
    models: {
      ...capabilities.models,
      [modelKey(anthropic)]: {
        display_name: "pinned-anthropic",
        prompt_cache_diagnostics: false,
        explicit_cache_mode: false,
        supported_settings: ["temperature", "thinking_budget"],
        unsupported_settings: {},
        reasoning_efforts: [],
      },
    },
  };
  expect(configureModel(anthropic, "", anthropicCapabilities).settings).toEqual({
    thinking_budget: 2048,
  });
});

test("explicit defaults persist; launching keeps fresh budgets and uses run-only settings", async ({
  page,
}) => {
  const bootstrap = await (await page.request.get("/api/bootstrap")).json();
  const headers = { "X-BH-Operator": bootstrap.operator_token };
  await awaitIdleWorker(page, bootstrap.operator_token);
  const original = bootstrap.config;
  const models = {
    sol,
    astra,
    luna: luna,
    "luna-tools": luna,
    "terra-tools": { ...terra, settings: { ...terra.settings } },
    "terra-cache": terra,
  };
  const putSettings = async (body: unknown) => {
    const response = await page.request.put("/api/settings", {
      headers,
      data: body,
    });
    expect(response.ok()).toBe(true);
  };
  await putSettings({ models, budgets: original.budgets, skills: "none" });
  try {
    const launches: any[] = [];
    await page.route("**/api/runs", async (route) => {
      launches.push(route.request().postDataJSON());
      await route.fulfill({ json: { episode_id: "a".repeat(32) } });
    });
    await page.goto("/");
    const picker = page.getByLabel("Model", { exact: true });
    await expect(picker.locator("optgroup[label='Models'] option")).toHaveCount(
      4,
    );
    for (const configured of [sol, astra]) {
      await picker.selectOption(modelKey(configured));
      await expect(
        page.getByLabel("Reasoning effort", { exact: true }),
      ).toHaveValue("medium");
      await expect(
        page.getByLabel("Reasoning effort", { exact: true }).locator("option"),
      ).toHaveText(
        configured === astra
          ? ["Low", "Medium", "High", "Extra high", "Max"]
          : ["None", "Low", "Medium", "High", "Extra high", "Max"],
      );
    }
    await expect(picker).not.toContainText("terra-cache");
    await picker.selectOption(modelKey(terra));
    await expect(
      page.getByLabel("Reasoning effort", { exact: true }),
    ).toHaveValue("medium");
    await page
      .getByLabel("Reasoning effort", { exact: true })
      .selectOption("high");
    await page
      .getByRole("button", { name: "Save model defaults", exact: true })
      .click();
    await expect(page.getByRole("status")).toHaveText(
      "Model defaults saved. No run started.",
    );
    expect(launches).toHaveLength(0);
    await page.reload();
    await picker.selectOption(modelKey(terra));
    await expect(
      page.getByLabel("Reasoning effort", { exact: true }),
    ).toHaveValue("high");
    // A separate operator changes limits after this browser loaded them.
    const fresh = (await (await page.request.get("/api/bootstrap")).json())
      .config;
    const budgets = {
      ...fresh.budgets,
      paid_calls_enabled: false,
      max_episode_cost_usd: 0.5,
      max_batch_cost_usd: 0.75,
    };
    await putSettings({ models: fresh.models, budgets, skills: "none" });
    await page
      .getByLabel("Reasoning effort", { exact: true })
      .selectOption("max");
    await awaitIdleWorker(page, bootstrap.operator_token);
    const created = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/runs") &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: /Start test episode/ }).click();
    const response = await created;
    expect(response.ok(), await response.text()).toBe(true);
    await expect(page.getByRole("status").filter({ hasText: "Run created" })).toBeVisible();
    expect(launches).toEqual([
      { agent: modelKey(terra), model_settings: { ...terra.settings, reasoning_effort: "max" }, model_revision: fresh.model_revisions[modelKey(terra)], offline: true, deck: "RED", stake: "GOLD", seed: null },
    ]);
    const saved = (await (await page.request.get("/api/bootstrap")).json())
      .config;
    expect(saved.budgets).toEqual(budgets);
    expect(saved.skills).toBe("none");
    expect(saved.models[modelKey(terra)].settings).toEqual({
      ...terra.settings,
      reasoning_effort: "high",
    });
    expect(saved.models["terra-tools"]).toMatchObject(models["terra-tools"]);
    expect(saved.models["terra-cache"]).toMatchObject(terra);
    await page
      .getByRole("button", { name: "Batches & reports", exact: true })
      .click();
    await page.route("**/api/panels", (route) =>
      route.fulfill({
        json:
          route.request().method() === "POST"
            ? { panel_id: "test-panel", count: 20 }
            : [{ panel_id: "test-panel", count: 20 }],
      }),
    );
    let planned: any;
    await page.route("**/api/batches", (route) => {
      if (route.request().method() === "POST")
        planned = route.request().postDataJSON();
      return route.fulfill({
        json:
          route.request().method() === "POST" ? { batch_id: "test-batch" } : [],
      });
    });
    await page
      .getByRole("button", { name: "Generate 20 private development seeds" })
      .click();
    await page.getByLabel("Heuristic baseline", { exact: true }).uncheck();
    await page.getByLabel("Random legal baseline", { exact: true }).uncheck();
    await page.getByLabel("GPT-5.6 Terra · OpenAI", { exact: true }).check();
    // New plans use the one current harness for every selected model.
    await page.getByLabel("GPT-5.6 Sol · OpenAI", { exact: true }).check();
    await page
      .getByRole("button", { name: "Freeze plan · 2 replicates" })
      .click();
    await expect
      .poll(() => planned)
      .toEqual({
        panel_id: "test-panel",
        agents: [modelKey(terra), modelKey(sol)],
        replicates: 2,
      });
    expect(launches).toHaveLength(1);
    const plannedSettings = (
      await (await page.request.get("/api/bootstrap")).json()
    ).config;
    expect(plannedSettings.models[modelKey(sol)].settings).toEqual(
      sol.settings,
    );
    expect(plannedSettings.models.sol).toEqual(sol);

    await page
      .getByRole("button", { name: "Models & budgets", exact: true })
      .click();
    await page
      .getByText("GPT-6 Astra · OpenAI", { exact: true })
      .locator("..")
      .getByRole("button", { name: "Edit connection" })
      .click();
    // Unsupported saved keys in the JSON field cannot reintroduce retired settings.
    await page
      .getByLabel("Additional model settings (JSON)")
      .fill(JSON.stringify({ ...astra.settings, retired_setting: "ignored" }));
    await page.getByRole("button", { name: "Save model", exact: true }).click();
    await expect(page.getByRole("status")).toHaveText(
      "Model configuration saved.",
    );
    const edited = (await (await page.request.get("/api/bootstrap")).json())
      .config;
    expect(
      edited.models[modelKey(astra)].settings.retired_setting,
    ).toBeUndefined();
    expect(edited.models.astra).toEqual(astra);
    expect(launches).toHaveLength(1);
    await page.getByRole("button", { name: "Runs", exact: true }).click();
    await picker.selectOption(modelKey(luna));
    await expect(page.getByText(/Configure a supported model/)).toBeVisible();
    await expect(
      page.getByRole("button", { name: /Start test episode/ }),
    ).toBeDisabled();
    await page.setViewportSize({ width: 390, height: 844 });
    await page
      .getByRole("heading", { name: "Start a run", exact: true })
      .locator("..")
      .screenshot({
        path: test.info().outputPath("model-picker-mobile.png"),
      });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.setViewportSize({ width: 1440, height: 1100 });
    await picker.selectOption(modelKey(terra));
    await page
      .getByRole("heading", { name: "Start a run", exact: true })
      .locator("..")
      .screenshot({
        path: test.info().outputPath("model-picker-desktop.png"),
      });
  } finally {
    await putSettings({
      models: original.models,
      budgets: original.budgets,
      skills: original.skills,
    });
  }
});

test("a rejected explicit defaults save never starts a run", async ({ page }) => {
  await page.route("**/api/bootstrap", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.config.models = { "terra-cache": terra };
    body.config.model_capabilities = capabilities;
    await route.fulfill({ json: body });
  });
  let launches = 0;
  await page.route("**/api/settings", (route) =>
    route.fulfill({ status: 400, json: { error: "WORKER_BUSY" } }),
  );
  await page.route("**/api/runs", (route) => {
    launches++;
    return route.fulfill({ json: { episode_id: "b".repeat(32) } });
  });
  await page.goto("/");
  await page.getByLabel("Model", { exact: true }).selectOption(modelKey(terra));
  await page.getByRole("button", { name: "Save model defaults", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("WORKER_BUSY");
  expect(launches).toBe(0);
});

test("a launch sends the configured alias the picker shows, even for integer-like keys", async ({ page }) => {
  // Object order lists "2" before "10"; Python and saved JSON keep "10" first.
  const ten = { ...terra, input_usd_per_million: 3, settings: { ...terra.settings, reasoning_effort: "high" } };
  const two = { ...terra, input_usd_per_million: 1, settings: { ...terra.settings, reasoning_effort: "low" } };
  await page.route("**/api/bootstrap", async (route) => {
    const body = await (await route.fetch()).json();
    body.config.models = { "10": ten, "2": two };
    body.config.model_revisions = { "10": "revision-ten", "2": "revision-two" };
    body.config.model_capabilities = capabilities;
    await route.fulfill({ json: body });
  });
  const launches: any[] = [];
  await page.route("**/api/runs", async (route) => {
    launches.push(route.request().postDataJSON());
    await route.fulfill({ json: { episode_id: "c".repeat(32) } });
  });
  await page.route("**/api/episodes", (route) => route.fulfill({ json: [] }));
  await page.goto("/");
  expect(modelCatalog({ "10": ten, "2": two })[modelKey(terra)]).toEqual(two);
  await page.getByLabel("Model", { exact: true }).selectOption(modelKey(terra));
  await expect(page.getByLabel("Reasoning effort", { exact: true })).toHaveValue("low");
  await page.getByRole("button", { name: /Start test episode/ }).click();
  await expect.poll(() => launches).toHaveLength(1);
  expect(launches[0]).toMatchObject({ agent: "2", model_settings: two.settings, model_revision: "revision-two" });
});

test("a launch refused because the shown alias changed refreshes the picker and starts nothing", async ({ page }) => {
  // "2" shows Terra at $1; before Start, another operator rebinds "2" to Sol at $9.
  const shown = { ...terra, input_usd_per_million: 1 };
  const rebound = { ...sol, input_usd_per_million: 9 };
  let current = { models: { "2": shown }, model_revisions: { "2": "revision-terra" } };
  let bootstraps = 0;
  await page.route("**/api/bootstrap", async (route) => {
    bootstraps++;
    const body = await (await route.fetch()).json();
    body.config = { ...body.config, ...current, model_capabilities: capabilities };
    await route.fulfill({ json: body });
  });
  const launches: any[] = [];
  await page.route("**/api/runs", async (route) => {
    launches.push(route.request().postDataJSON());
    await route.fulfill({ status: 400, json: { error: "MODEL_CONFIG_CHANGED" } });
  });
  await page.route("**/api/episodes", (route) => route.fulfill({ json: [] }));
  await page.goto("/");
  const picker = page.getByLabel("Model", { exact: true });
  await picker.selectOption(modelKey(terra));
  current = { models: { "2": rebound }, model_revisions: { "2": "revision-sol" } };
  const before = bootstraps;
  await page.getByRole("button", { name: /Start test episode/ }).click();
  await expect(page.getByRole("alert")).toContainText("changed");
  expect(launches).toEqual([expect.objectContaining({ agent: "2", model_revision: "revision-terra" })]);
  expect(bootstraps).toBeGreaterThan(before);
  await expect(picker.locator("optgroup[label='Models'] option")).toHaveText(["GPT-5.6 Sol · OpenAI"]);
  expect(launches).toHaveLength(1);
});

for (const [name, revisions] of [
  ["an absent revision map", undefined],
  ["an absent revision key", {}],
  ["a null revision", { "2": null }],
] as const) {
  test(`a shown model with ${name} is never launched, even after a refresh`, async ({ page }) => {
    let bootstraps = 0;
    await page.route("**/api/bootstrap", async (route) => {
      bootstraps++;
      const body = await (await route.fetch()).json();
      const { model_revisions: _ignored, ...config } = body.config;
      body.config = { ...config, models: { "2": terra }, model_capabilities: capabilities, ...(revisions === undefined ? {} : { model_revisions: revisions }) };
      await route.fulfill({ json: body });
    });
    const launches: unknown[] = [];
    await page.route("**/api/runs", async (route) => {
      launches.push(route.request().postDataJSON());
      await route.fulfill({ json: { episode_id: "d".repeat(32) } });
    });
    await page.route("**/api/episodes", (route) => route.fulfill({ json: [] }));
    await page.goto("/");
    await page.getByLabel("Model", { exact: true }).selectOption(modelKey(terra));
    const before = bootstraps;
    await page.getByRole("button", { name: /Start test episode/ }).click();
    await expect(page.getByRole("alert")).toContainText(/confirm/i);
    expect(bootstraps).toBeGreaterThan(before);
    expect(launches).toEqual([]);
  });
}

test("a refusal whose refresh lacks revisions keeps the shown choice and starts nothing", async ({ page }) => {
  let current: Record<string, unknown> = { models: { "2": { ...terra, input_usd_per_million: 1 } }, model_revisions: { "2": "revision-terra" } };
  await page.route("**/api/bootstrap", async (route) => {
    const body = await (await route.fetch()).json();
    const { model_revisions: _ignored, ...config } = body.config;
    body.config = { ...config, ...current, model_capabilities: capabilities };
    await route.fulfill({ json: body });
  });
  const launches: any[] = [];
  await page.route("**/api/runs", async (route) => {
    launches.push(route.request().postDataJSON());
    await route.fulfill({ status: 400, json: { error: "MODEL_CONFIG_CHANGED" } });
  });
  await page.route("**/api/episodes", (route) => route.fulfill({ json: [] }));
  await page.goto("/");
  const picker = page.getByLabel("Model", { exact: true });
  await picker.selectOption(modelKey(terra));
  current = { models: { "2": { ...sol, input_usd_per_million: 9 } } };
  await page.getByRole("button", { name: /Start test episode/ }).click();
  await expect(page.getByRole("alert")).toContainText(/confirm/i);
  await expect(picker.locator("optgroup[label='Models'] option")).toHaveText(["GPT-5.6 Terra · OpenAI"]);
  expect(launches).toEqual([expect.objectContaining({ agent: "2", model_revision: "revision-terra" })]);
});

test("a model removed and restored after it was shown must be chosen again before any launch", async ({ page }) => {
  // "2" is Terra at $1; settings then remove every model, and later restore "2" at $9.
  let current: Record<string, unknown> = { models: { "2": { ...terra, input_usd_per_million: 1 } }, model_revisions: { "2": "revision-shown" } };
  let bootstraps = 0;
  await page.route("**/api/bootstrap", async (route) => {
    bootstraps++;
    const body = await (await route.fetch()).json();
    const { model_revisions: _ignored, ...config } = body.config;
    body.config = { ...config, ...current, model_capabilities: capabilities };
    await route.fulfill({ json: body });
  });
  const launches: any[] = [];
  await page.route("**/api/runs", async (route) => {
    launches.push(route.request().postDataJSON());
    await route.fulfill({ status: 400, json: { error: "MODEL_CONFIG_CHANGED" } });
  });
  await page.route("**/api/episodes", (route) => route.fulfill({ json: [] }));
  await page.goto("/");
  const picker = page.getByLabel("Model", { exact: true });
  const start = page.getByRole("button", { name: /Start test episode/ });
  await picker.selectOption(modelKey(terra));
  current = { models: {}, model_revisions: {} };
  await start.click();
  await expect(page.getByRole("alert")).toContainText("changed");
  await expect(picker.locator("optgroup[label='Models'] option")).toHaveCount(0);
  current = { models: { "2": { ...terra, input_usd_per_million: 9, settings: { ...terra.settings, reasoning_effort: "high" } } }, model_revisions: { "2": "revision-restored" } };
  const before = bootstraps;
  await start.click();
  // Either way the browser refetches; any second launch request would already have been sent.
  await expect.poll(() => bootstraps).toBeGreaterThan(before);
  // Only the first, bound request was ever sent; it was refused, so nothing started.
  expect(launches).toEqual([expect.objectContaining({ agent: "2", model_revision: "revision-shown" })]);
  await expect(page.getByRole("alert")).toContainText(/choose/i);
  await expect(picker.locator("optgroup[label='Models'] option")).toHaveText(["GPT-5.6 Terra · OpenAI"]);
  await expect(picker).not.toHaveValue(modelKey(terra));
});

for (const [name, models] of [["a null", null], ["an array", []], ["a string", "models"]] as const) {
  test(`a refresh with ${name} model collection is not installed`, async ({ page }) => {
    let current: Record<string, unknown> = { models: { "2": terra }, model_revisions: { "2": "revision-terra" } };
    await page.route("**/api/bootstrap", async (route) => {
      const body = await (await route.fetch()).json();
      const { model_revisions: _ignored, ...config } = body.config;
      body.config = { ...config, ...current, model_capabilities: capabilities };
      await route.fulfill({ json: body });
    });
    const launches: unknown[] = [];
    await page.route("**/api/runs", async (route) => {
      launches.push(route.request().postDataJSON());
      await route.fulfill({ status: 400, json: { error: "MODEL_CONFIG_CHANGED" } });
    });
    await page.route("**/api/episodes", (route) => route.fulfill({ json: [] }));
    await page.goto("/");
    const picker = page.getByLabel("Model", { exact: true });
    await picker.selectOption(modelKey(terra));
    current = { models, model_revisions: {} };
    await page.getByRole("button", { name: /Start test episode/ }).click();
    await expect(page.getByRole("alert")).toContainText(/could not be confirmed/i);
    await expect(picker.locator("optgroup[label='Models'] option")).toHaveText(["GPT-5.6 Terra · OpenAI"]);
    expect(launches).toHaveLength(1);
  });
}

for (const reserved of ["heuristic", "random_legal", "human"]) {
  test(`a model aliased "${reserved}" launches through its other alias, never as the built-in player`, async ({ page }) => {
    const first = { ...terra, input_usd_per_million: 1 };
    const second = { ...terra, input_usd_per_million: 2 };
    await page.route("**/api/bootstrap", async (route) => {
      const body = await (await route.fetch()).json();
      body.config = { ...body.config, workbench: false, models: { [reserved]: first, terra2: second }, model_revisions: { [reserved]: "revision-reserved", terra2: "revision-terra2" }, model_capabilities: capabilities };
      body.workbench = false;
      await route.fulfill({ json: body });
    });
    const launches: any[] = [];
    await page.route("**/api/runs", async (route) => {
      launches.push(route.request().postDataJSON());
      await route.fulfill({ json: { episode_id: "e".repeat(32) } });
    });
    await page.route("**/api/episodes", (route) => route.fulfill({ json: [] }));
    await page.goto("/");
    await page.getByLabel("Model", { exact: true }).selectOption(modelKey(terra));
    await page.getByRole("button", { name: /Start test episode/ }).click();
    await expect.poll(() => launches.length).toBe(1);
    expect(launches[0]).toMatchObject({ agent: "terra2", model_revision: "revision-terra2" });
  });
}

test("a model configured only under a built-in player name is not offered", async ({ page }) => {
  await page.route("**/api/bootstrap", async (route) => {
    const body = await (await route.fetch()).json();
    body.config = { ...body.config, models: { human: terra }, model_revisions: { human: "revision-human" }, model_capabilities: capabilities };
    await route.fulfill({ json: body });
  });
  await page.route("**/api/episodes", (route) => route.fulfill({ json: [] }));
  await page.goto("/");
  const picker = page.getByLabel("Model", { exact: true });
  await expect(picker.locator("optgroup[label='Models'] option")).toHaveCount(0);
  await expect(page.getByText(/built-in player name/)).toBeVisible();
});

// A validated legacy config from before reserved names were refused: one Luna model under
// the built-in name "human" and under "luna2".
async function legacyAliasApp(page: import("@playwright/test").Page) {
  let stored: Record<string, any> = { human: { ...luna, input_usd_per_million: 1 }, luna2: { ...luna, input_usd_per_million: 2 } };
  const saves: any[] = [];
  const configFor = (base: any) => ({
    ...base, models: stored, skills: "none", model_capabilities: capabilities,
    model_revisions: Object.fromEntries(Object.keys(stored).map((key) => [key, "revision-" + key])),
  });
  await page.route("**/api/bootstrap", async (route) => {
    const body = await (await route.fetch()).json();
    body.config = configFor(body.config);
    await route.fulfill({ json: body });
  });
  await page.route("**/api/settings", async (route) => {
    const body = route.request().postDataJSON();
    saves.push(body);
    stored = body.models;
    await route.fulfill({ json: configFor({ budgets: body.budgets, skills: body.skills, models: body.models }) });
  });
  await page.route("**/api/episodes", (route) => route.fulfill({ json: [] }));
  await page.goto("/");
  await page.getByRole("button", { name: "Models & budgets", exact: true }).click();
  return { saves, repair: page.getByRole("region", { name: "Aliases using built-in player names" }) };
}

test("the repair lists each model alias that uses a built-in player name", async ({ page }) => {
  const { repair } = await legacyAliasApp(page);
  await expect(repair.getByRole("listitem")).toHaveCount(1);
  await expect(repair.getByRole("listitem")).toContainText("human");
  await expect(repair.getByRole("listitem")).toContainText("gpt-5.6-luna");
});

test("renaming a reserved alias moves its record to the new key and leaves other models alone", async ({ page }) => {
  const { saves, repair } = await legacyAliasApp(page);
  await expect(repair.getByRole("listitem")).toHaveCount(1);
  await repair.getByLabel("New alias for human").fill("luna-legacy");
  await repair.getByRole("button", { name: "Rename human" }).click();
  await repair.getByRole("button", { name: "Repair aliases" }).click();
  await expect.poll(() => saves.length).toBe(1);
  expect(Object.keys(saves[0].models).sort()).toEqual(["luna-legacy", "luna2"]);
  expect(saves[0].models["luna-legacy"]).toEqual({ ...luna, input_usd_per_million: 1 });
  expect(saves[0].models.luna2).toEqual({ ...luna, input_usd_per_million: 2 });
  await expect(repair.getByRole("listitem")).toHaveCount(0);
});

test("removing a reserved alias saves the models without it", async ({ page }) => {
  const { saves, repair } = await legacyAliasApp(page);
  await expect(repair.getByRole("listitem")).toHaveCount(1);
  await repair.getByRole("button", { name: "Remove human" }).click();
  await repair.getByRole("button", { name: "Repair aliases" }).click();
  await expect.poll(() => saves.length).toBe(1);
  expect(Object.keys(saves[0].models)).toEqual(["luna2"]);
});

for (const [name, value] of [["a built-in name", "random_legal"], ["a configured alias", "luna2"], ["an empty name", ""], ["a padded name", " luna-legacy"]]) {
  test(`renaming a reserved alias to ${name} sends nothing`, async ({ page }) => {
    const { saves, repair } = await legacyAliasApp(page);
    await expect(repair.getByRole("listitem")).toHaveCount(1);
    await repair.getByLabel("New alias for human").fill(value);
    await repair.getByRole("button", { name: "Rename human" }).click();
    await repair.getByRole("button", { name: "Repair aliases" }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    expect(saves).toEqual([]);
  });
}

test("after a rename, ordinary saves carry no reserved alias", async ({ page }) => {
  const { saves, repair } = await legacyAliasApp(page);
  await expect(repair.getByRole("listitem")).toHaveCount(1);
  await repair.getByLabel("New alias for human").fill("luna-legacy");
  await repair.getByRole("button", { name: "Rename human" }).click();
  await repair.getByRole("button", { name: "Repair aliases" }).click();
  await expect.poll(() => saves.length).toBe(1);
  await page.getByRole("button", { name: "Save spending limits", exact: true }).click();
  await expect.poll(() => saves.length).toBe(2);
  await page.getByRole("button", { name: "Save skill access", exact: true }).click();
  await expect.poll(() => saves.length).toBe(3);
  for (const save of saves.slice(1)) expect(Object.keys(save.models).sort()).toEqual(["luna-legacy", "luna2"]);
});

// Several reserved aliases against the real settings route, which refuses any save that still
// carries one: only a single save that repairs all of them can succeed.
test.describe("repairing several reserved aliases on a real server", () => {
  let server: LegacyServer | undefined;
  test.afterEach(async () => { await server?.stop(); server = undefined; });

  async function open(page: import("@playwright/test").Page, reserved: string[]) {
    server = await startLegacyServer(reserved);
    const puts: any[] = [];
    page.on("request", (request) => { if (request.method() === "PUT" && new URL(request.url()).pathname === "/api/settings") puts.push(request.postDataJSON()); });
    await page.goto(server.origin + "/");
    await page.getByRole("button", { name: "Models & budgets", exact: true }).click();
    const repair = page.getByRole("region", { name: "Aliases using built-in player names" });
    await expect(repair.getByRole("listitem")).toHaveCount(reserved.length);
    return { server, puts, repair };
  }

  test("recording each alias's decision sends no request", async ({ page }) => {
    const { puts, repair } = await open(page, ["human", "heuristic"]);
    await repair.getByLabel("New alias for human").fill("luna-human");
    await repair.getByRole("button", { name: "Rename human" }).click();
    await repair.getByRole("button", { name: "Remove heuristic" }).click();
    await expect(repair.getByRole("listitem")).toHaveCount(2);
    expect(puts).toEqual([]);
    await expect(repair.getByRole("button", { name: "Repair aliases" })).toBeEnabled();
  });

  test("one repair save of two renames is accepted, then an ordinary save follows", async ({ page }) => {
    const { server, puts, repair } = await open(page, ["human", "heuristic"]);
    await expect(repair.getByRole("button", { name: "Repair aliases" })).toBeVisible();
    for (const key of ["human", "heuristic"]) {
      await repair.getByLabel(`New alias for ${key}`).fill(`luna-${key}`);
      await repair.getByRole("button", { name: `Rename ${key}` }).click();
    }
    const saved = page.waitForResponse((response) => response.request().method() === "PUT" && response.url().endsWith("/api/settings"));
    await repair.getByRole("button", { name: "Repair aliases" }).click();
    expect((await saved).status()).toBe(200);
    const models = server.persisted().models;
    expect(Object.keys(models).sort()).toEqual(["luna-heuristic", "luna-human", "luna2"]);
    expect(models["luna-human"]).toEqual(server.seeded.models.human);
    expect(models["luna-heuristic"]).toEqual(server.seeded.models.heuristic);
    expect(models.luna2).toEqual(server.seeded.models.luna2);
    const ordinary = page.waitForResponse((response) => response.request().method() === "PUT" && response.url().endsWith("/api/settings"));
    await page.getByRole("button", { name: "Save spending limits", exact: true }).click();
    expect((await ordinary).status()).toBe(200);
    expect(puts).toHaveLength(2);
  });

  test("one repair save mixes renames and a removal across three reserved aliases", async ({ page }) => {
    const { server, puts, repair } = await open(page, ["human", "heuristic", "random_legal"]);
    await expect(repair.getByRole("button", { name: "Repair aliases" })).toBeVisible();
    await repair.getByLabel("New alias for human").fill("luna-human");
    await repair.getByRole("button", { name: "Rename human" }).click();
    await repair.getByRole("button", { name: "Remove heuristic" }).click();
    await repair.getByLabel("New alias for random_legal").fill("luna-random");
    await repair.getByRole("button", { name: "Rename random_legal" }).click();
    const saved = page.waitForResponse((response) => response.request().method() === "PUT" && response.url().endsWith("/api/settings"));
    await repair.getByRole("button", { name: "Repair aliases" }).click();
    expect((await saved).status()).toBe(200);
    const models = server.persisted().models;
    expect(Object.keys(models).sort()).toEqual(["luna-human", "luna-random", "luna2"]);
    expect(models["luna-human"]).toEqual(server.seeded.models.human);
    expect(models["luna-random"]).toEqual(server.seeded.models.random_legal);
    expect(puts).toHaveLength(1);
  });

  test("two quick repair clicks while bootstrap is held send exactly one save", async ({ page }) => {
    const { server, puts, repair } = await open(page, ["human", "heuristic"]);
    await expect(repair.getByRole("button", { name: "Repair aliases" })).toBeVisible();
    await repair.getByRole("button", { name: "Remove human" }).click();
    await repair.getByRole("button", { name: "Remove heuristic" }).click();
    let release = () => {};
    const held = new Promise<void>((resolve) => { release = resolve; });
    let bootstraps = 0;
    await page.route("**/api/bootstrap", async (route) => { bootstraps++; await held; await route.continue(); });
    await page.evaluate(() => {
      const button = Array.from(document.querySelectorAll("button")).find((candidate) => candidate.textContent === "Repair aliases")!;
      button.click();
      button.click();
    });
    await expect.poll(() => bootstraps).toBe(1);
    const saved = page.waitForResponse((response) => response.request().method() === "PUT" && response.url().endsWith("/api/settings"));
    release();
    expect((await saved).status()).toBe(200);
    await expect(repair).toHaveCount(0);
    expect(puts).toHaveLength(1);
    expect(bootstraps).toBe(1);
    expect(Object.keys(server.persisted().models)).toEqual(["luna2"]);
  });

  for (const [name, targets] of [["duplicate new names", ["luna-legacy", "luna-legacy"]], ["a configured name", ["luna2", "luna-heuristic"]]] as const) {
    test(`a repair with ${name} sends no save`, async ({ page }) => {
      const { puts, repair } = await open(page, ["human", "heuristic"]);
    await expect(repair.getByRole("button", { name: "Repair aliases" })).toBeVisible();
      for (const [index, key] of ["human", "heuristic"].entries()) {
        await repair.getByLabel(`New alias for ${key}`).fill(targets[index]);
        await repair.getByRole("button", { name: `Rename ${key}` }).click();
      }
      await repair.getByRole("button", { name: "Repair aliases" }).click();
      await expect(page.getByRole("alert")).toBeVisible();
      expect(puts).toEqual([]);
    });
  }

  test("a repair whose reserved alias vanished meanwhile sends no save", async ({ page }) => {
    const { puts, repair } = await open(page, ["human", "heuristic"]);
    await expect(repair.getByRole("button", { name: "Repair aliases" })).toBeVisible();
    await repair.getByRole("button", { name: "Remove human" }).click();
    await repair.getByRole("button", { name: "Remove heuristic" }).click();
    // Another session's view of the latest config: "heuristic" is gone.
    await page.route("**/api/bootstrap", async (route) => {
      const body = await (await route.fetch()).json();
      delete body.config.models.heuristic;
      await route.fulfill({ json: body });
    });
    await repair.getByRole("button", { name: "Repair aliases" }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    expect(puts).toEqual([]);
  });

  test("the repair stays disabled until every reserved alias has a decision", async ({ page }) => {
    const { puts, repair } = await open(page, ["human", "heuristic"]);
    await expect(repair.getByRole("button", { name: "Repair aliases" })).toBeDisabled();
    await repair.getByRole("button", { name: "Remove human" }).click();
    await expect(repair.getByRole("button", { name: "Repair aliases" })).toBeDisabled();
    expect(puts).toEqual([]);
  });
});

// The legacy-server helper itself: every startup failure must leave no process and no temp dir.
test.describe("legacy server startup failures", () => {
  const legacyDirs = () => readdirSync(tmpdir()).filter((name) => name.startsWith("bh-legacy-")).sort();
  const node = process.execPath;
  // Startup must settle within this bound on every path, so a hang fails an assertion.
  const settled = <T,>(promise: Promise<T>, ms = 15000) => Promise.race([promise, new Promise<"pending">((resolve) => setTimeout(() => resolve("pending"), ms))]);
  async function startFailure(options: Parameters<typeof startLegacyServer>[1]) {
    // Cleanup must delete only the directory this start created: a same-prefix directory and an
    // unrelated file nearby both survive it.
    const sentinelDir = mkdtempSync(join(tmpdir(), "bh-legacy-sentinel-"));
    const sentinelFile = join(tmpdir(), `bh-sentinel-${process.pid}-${Date.now()}.txt`);
    writeFileSync(sentinelFile, "keep");
    const before = legacyDirs();
    const outcome = await settled(startLegacyServer(["human"], options).then(async (server) => { await server.stop(); return "started"; }, (error: Error) => error));
    const after = legacyDirs();
    const sentinelsSurvived = existsSync(sentinelDir) && existsSync(sentinelFile);
    rmSync(sentinelDir, { recursive: true, force: true });
    rmSync(sentinelFile, { force: true });
    return { outcome, before, after, sentinelsSurvived };
  }
  const pidIn = (error: Error) => Number(/fake pid (\d+)/.exec(error.message)?.[1]);
  type Probe = (pid: number, signal: 0) => unknown;
  // Present when the probe succeeds or is refused (EPERM: it exists); absent only on ESRCH.
  const alive = (pid: number, probe: Probe = process.kill) => {
    try { probe(pid, 0); return true; } catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (code === "EPERM") return true;
      if (code === "ESRCH") return false;
      throw new Error(`Could not probe pid ${pid}: ${code ?? (error as Error).message}`);
    }
  };
  const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

  test("a readiness request that hangs fails at the deadline with the server's stderr", async () => {
    // Accepts connections but never answers.
    const hung = `require("net").createServer(() => {}).listen(+process.argv[1], "127.0.0.1", () => console.error("fake pid " + process.pid));`;
    const started = Date.now();
    const { outcome, before, after, sentinelsSurvived } = await startFailure({ command: (_dir, port) => [node, ["-e", hung, String(port)]], readyTimeoutMs: 2000, fetchTimeoutMs: 300 });
    expect(outcome).toBeInstanceOf(Error);
    const error = outcome as Error;
    expect(error.message).toMatch(/did not start within/);
    expect(error.message).toContain("fake pid");
    expect(Date.now() - started).toBeLessThan(10000);
    expect(Number.isFinite(pidIn(error))).toBe(true);
    expect(alive(pidIn(error))).toBe(false);
    expect(after).toEqual(before);
    expect(sentinelsSurvived).toBe(true);
  });

  test("a server that exits early fails with its stderr and leaves nothing behind", async () => {
    const early = `console.error("fake pid " + process.pid); process.exit(3);`;
    const { outcome, before, after, sentinelsSurvived } = await startFailure({ command: () => [node, ["-e", early]] });
    expect(outcome).toBeInstanceOf(Error);
    expect((outcome as Error).message).toMatch(/exited/);
    expect((outcome as Error).message).toContain("fake pid");
    expect(after).toEqual(before);
    expect(sentinelsSurvived).toBe(true);
  });

  test("a server that cannot be spawned fails clearly and leaves no temp dir", async () => {
    const { outcome, before, after, sentinelsSurvived } = await startFailure({ command: () => ["/nonexistent/bh-legacy-python", []] });
    expect(outcome).toBeInstanceOf(Error);
    expect((outcome as Error).message).toMatch(/could not be started/);
    expect(after).toEqual(before);
    expect(sentinelsSurvived).toBe(true);
  });

  // A real fake server whose own child inherits its stderr: the pipe stays open after it exits.
  const grandchild = `const { spawn } = require("child_process"); const g = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { stdio: ["ignore", "ignore", "inherit"] }); require("fs").writeFileSync(process.argv[1], String(g.pid));`;
  // Scope: guards against false passes, signalling a process the test did not create, leaks and lost causes.
  // Out of scope: hostile same-UID receipt planting, PID reuse before the signal-0 probe, other kill errors, multiple workers.
  // Reads a grandchild PID receipt the test itself wrote and kills that process. It signals only a
  // canonical decimal id in (1, 2147483647] that is neither this process nor its parent (0 or a
  // negative id would reach a whole process group), and reports only a confirmed outcome:
  // "gone" if there was no such process, "dead" once the probe stops finding it. Anything else
  // throws with the pid, the error code and the receipt path.
  const killGrandchild = async (pidFile: string, send: (pid: number, signal: NodeJS.Signals) => unknown = process.kill, probe: Probe = process.kill, boundMs = 3000): Promise<"dead" | "gone"> => {
    const text = existsSync(pidFile) ? readFileSync(pidFile, "utf8") : undefined;
    const pid = text !== undefined && /^[1-9][0-9]*$/.test(text) ? Number(text) : NaN;
    if (!(pid > 1 && pid <= 2147483647) || pid === process.pid || pid === process.ppid)
      throw new Error(`Invalid grandchild receipt ${pidFile}: ${JSON.stringify(text)}`);
    try { send(pid, "SIGKILL"); } catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (code === "ESRCH") return "gone";
      throw new Error(`Could not kill grandchild ${pid} from receipt ${pidFile}: ${code ?? (error as Error).message}`);
    }
    const deadline = Date.now() + boundMs;
    do {
      if (!alive(pid, probe)) return "dead";
      await sleep(50);
    } while (Date.now() < deadline);
    throw new Error(`Grandchild ${pid} from receipt ${pidFile}: SIGKILL delivered but death unconfirmed`);
  };

  // Signals the grandchild a test started and removes that test's receipt dir.
  const releaseReceipts = async (receipts: string, pidFile: string, send?: (pid: number, signal: NodeJS.Signals) => unknown, probe?: Probe) => {
    // The receipt's details travel in any error, so the dir always goes.
    try { expect(["dead", "gone"]).toContain(await killGrandchild(pidFile, send, probe)); } finally { rmSync(receipts, { recursive: true, force: true }); }
  };

  type Send = (pid: number, signal: NodeJS.Signals) => unknown;
  type Grandchild = { pidFile: string; start: (...args: Parameters<typeof startLegacyServer>) => Promise<LegacyServer> };
  // Test-only seams, so every acquisition, setup and cleanup failure can be exercised.
  type GrandchildDeps = { mkdtemp?: (prefix: string) => string; start?: typeof startLegacyServer; send?: Send; probe?: Probe; rm?: (dir: string) => void; boundMs?: number };
  // One guard for every test that may leave a grandchild: it owns the receipts dir, any server
  // the body starts through it, and the grandchild a receipt names.
  async function withGrandchild<T>(body: (grandchild: Grandchild) => Promise<T>, deps: GrandchildDeps = {}): Promise<T> {
    const receipts = (deps.mkdtemp ?? mkdtempSync)(join(tmpdir(), "bh-receipts-"));
    const pidFile = join(receipts, "grandchild.pid");
    let server: LegacyServer | undefined;
    try {
      return await body({ pidFile, start: async (...args) => (server = await (deps.start ?? startLegacyServer)(...args)) });
    } finally {
      await releaseReceipts(receipts, pidFile, deps.send, deps.probe);
      if (server) await server.stop();
    }
  }

  test("a server that exits while its child holds stderr open still fails startup and cleans up", async () => {
    const exits = `${grandchild} console.error("fake pid " + process.pid); process.exit(0);`;
    await withGrandchild(async ({ pidFile }) => {
      const { outcome, before, after, sentinelsSurvived } = await startFailure({ command: () => [node, ["-e", exits, pidFile]], stopTimeoutMs: 1000 });
      expect(outcome).toBeInstanceOf(Error);
      expect((outcome as Error).message).toMatch(/exited/);
      expect(after).toEqual(before);
      expect(sentinelsSurvived).toBe(true);
    });
  });

  test("a server that exits on shutdown while its child holds stderr open still fails startup within a bound", async () => {
    // It never answers, so startup times out and stops it; it exits on SIGTERM, but its own
    // child keeps the inherited stderr pipe open, so the pipe never closes.
    const hangs = `${grandchild.replace("process.argv[1]", "process.argv[2]")} require("net").createServer(() => {}).listen(+process.argv[1], "127.0.0.1", () => console.error("fake pid " + process.pid));`;
    await withGrandchild(async ({ pidFile }) => {
      const { outcome, before, after, sentinelsSurvived } = await startFailure({ command: (_dir, port) => [node, ["-e", hangs, String(port), pidFile]], readyTimeoutMs: 1500, fetchTimeoutMs: 300, stopTimeoutMs: 1000 });
      expect(outcome).toBeInstanceOf(Error);
      expect((outcome as Error).message).toMatch(/did not start within/);
      expect(Number.isFinite(pidIn(outcome as Error))).toBe(true);
      expect(alive(pidIn(outcome as Error))).toBe(false);
      expect(after).toEqual(before);
      expect(sentinelsSurvived).toBe(true);
    });
  });

  test("repeated stops share one shutdown that ends only after cleanup", async () => {
    const serves = `${grandchild.replace("process.argv[1]", "process.argv[2]")} require("http").createServer((_q, r) => r.end("{}")).listen(+process.argv[1], "127.0.0.1");`;
    await withGrandchild(async ({ pidFile, start }) => {
      // Started through the fixture, so a startup failure still releases the receipt dir.
      const server = await start(["human"], { command: (_dir, port) => [node, ["-e", serves, String(port), pidFile]], stopTimeoutMs: 1000 });
      const first = server.stop();
      const second = server.stop();
      expect(second).toBe(first);
      expect(Number.isFinite(server.pid)).toBe(true);
      const state = () => ({ dir: existsSync(server.dataDir), alive: alive(server.pid!) });
      const outcome = await settled(Promise.all([first.then(state), second.then(state)]));
      expect(outcome).toEqual([{ dir: false, alive: false }, { dir: false, alive: false }]);
    });
  });

  test("errors from signalling the child never escape and never skip cleanup", async () => {
    // A real process cannot fail kill() twice on demand, so this one case injects a fake child
    // whose every kill() emits two errors. It never signals a real process.
    const escaped: unknown[] = [];
    const record = (error: unknown) => { escaped.push(error); };
    process.on("uncaughtException", record);
    try {
      const fake = () => Object.assign(new EventEmitter(), {
        pid: 2147483646, exitCode: null, signalCode: null, stderr: new PassThrough(),
        kill(this: EventEmitter) { this.emit("error", new Error("kill failed")); this.emit("error", new Error("kill failed again")); return false; },
      }) as unknown as ChildProcess;
      const { outcome, before, after, sentinelsSurvived } = await startFailure({ spawn: fake, readyTimeoutMs: 1000, fetchTimeoutMs: 200, stopTimeoutMs: 300 });
      await sleep(500);
      expect(outcome).toBeInstanceOf(Error);
      expect((outcome as Error).message).toMatch(/did not start within/);
      // Its fake child never exits, so shutdown cannot confirm termination and must say so.
      expect((outcome as Error).message).toMatch(/termination unconfirmed/);
      expect(after).toEqual(before);
      expect(sentinelsSurvived).toBe(true);
      expect(escaped).toEqual([]);
    } finally {
      process.off("uncaughtException", record);
    }
  });

  // Fake children for kill outcomes no real process produces on demand. Their pid is never a
  // real target: kill() is the fake's own method.
  const fakeChild = (kill: (child: EventEmitter) => boolean, stderr: PassThrough = new PassThrough()) => () =>
    Object.assign(new EventEmitter(), { pid: 2147483646, exitCode: null, signalCode: null, stderr, kill(this: EventEmitter) { return kill(this); } }) as unknown as ChildProcess;
  const unconfirmed: [string, (child: EventEmitter) => boolean][] = [
    ["returns false", () => false],
    ["throws", () => { throw new Error("kill threw"); }],
    ["reports EPERM", (child) => { child.emit("error", Object.assign(new Error("kill EPERM"), { code: "EPERM" })); return false; }],
  ];
  for (const [name, kill] of unconfirmed) {
    test(`a child whose kill ${name} and never exits fails startup with termination unconfirmed`, async () => {
      const { outcome, before, after, sentinelsSurvived } = await startFailure({ spawn: fakeChild(kill), readyTimeoutMs: 1000, fetchTimeoutMs: 200, stopTimeoutMs: 300 });
      expect(outcome).toBeInstanceOf(Error);
      const error = outcome as Error & { pid?: number; dataDir?: string };
      expect(error.message).toMatch(/did not start within/);
      expect(error.message).toMatch(/termination unconfirmed/);
      expect(error.message).toContain("2147483646");
      expect(error.pid).toBe(2147483646);
      expect(typeof error.dataDir).toBe("string");
      expect(existsSync(error.dataDir!)).toBe(false);
      expect(after).toEqual(before);
      expect(sentinelsSurvived).toBe(true);
    });
  }

  test("a stderr that throws on destroy still removes the dir and keeps the startup cause", async () => {
    const stderr = Object.assign(new PassThrough(), { destroy(): never { throw new Error("destroy failed"); } });
    const exits = (child: EventEmitter) => { setImmediate(() => child.emit("exit", null, "SIGTERM")); return true; };
    const { outcome, before, after, sentinelsSurvived } = await startFailure({ spawn: fakeChild(exits, stderr), readyTimeoutMs: 1000, fetchTimeoutMs: 200, stopTimeoutMs: 300 });
    expect(outcome).toBeInstanceOf(Error);
    expect((outcome as Error).message).toMatch(/did not start within/);
    expect((outcome as Error).message).toContain("destroy failed");
    expect(after).toEqual(before);
    expect(sentinelsSurvived).toBe(true);
  });

  test("grandchild receipts signal only a validated process id", async () => {
    // Recorded, never sent: 0 or a negative id would signal a whole process group.
    const receipts = mkdtempSync(join(tmpdir(), "bh-receipts-"));
    const file = join(receipts, "grandchild.pid");
    try {
      for (const text of ["", "0", "-5", "1", "12abc", " 7", String(process.pid), String(process.ppid)]) {
        const sent: unknown[] = [];
        writeFileSync(file, text);
        const result = await settle(() => killGrandchild(file, (...args) => sent.push(args), gone));
        expect(result, JSON.stringify(text)).toHaveProperty("error");
        expect(sent, JSON.stringify(text)).toEqual([]);
      }
      const sent: unknown[] = [];
      writeFileSync(file, "424242");
      expect(await settle(() => killGrandchild(file, (...args) => sent.push(args), gone))).toEqual({ value: "dead" });
      expect(sent).toEqual([[424242, "SIGKILL"]]);
    } finally {
      rmSync(receipts, { recursive: true, force: true });
    }
  });

  // Recorders and probes only: none of these cases sends a real signal.
  const errno = (code: string) => Object.assign(new Error(`simulated ${code}`), { code });
  const gone: Probe = () => { throw errno("ESRCH"); };
  const present: Probe = () => true;
  const settle = (run: () => unknown) => Promise.resolve().then(run).then((value) => ({ value }), (error: Error) => ({ error: error.message }));
  async function withReceipt<T>(text: string, use: (file: string) => Promise<T>) {
    const dir = mkdtempSync(join(tmpdir(), "bh-receipts-"));
    const file = join(dir, "grandchild.pid");
    writeFileSync(file, text);
    try { return await use(file); } finally { rmSync(dir, { recursive: true, force: true }); }
  }

  for (const code of ["EPERM", "EINVAL"]) {
    test(`a grandchild kill that fails with ${code} reports the pid, the code and the receipt`, async () => {
      await withReceipt("424242", async (file) => {
        const result = await settle(() => killGrandchild(file, () => { throw errno(code); }, gone, 200));
        expect(result).toHaveProperty("error");
        const message = (result as { error: string }).error;
        expect(message).toContain("424242");
        expect(message).toContain(code);
        expect(message).toContain(file);
      });
    });
  }

  test("a grandchild kill that finds no such process reports it gone", async () => {
    await withReceipt("424242", async (file) => {
      expect(await settle(() => killGrandchild(file, () => { throw errno("ESRCH"); }, present, 200))).toEqual({ value: "gone" });
    });
  });

  test("a delivered grandchild kill reports death only once the probe stops finding it", async () => {
    await withReceipt("424242", async (file) => {
      expect(await settle(() => killGrandchild(file, () => true, gone, 200))).toEqual({ value: "dead" });
    });
    await withReceipt("424242", async (file) => {
      const result = await settle(() => killGrandchild(file, () => true, present, 200));
      expect(result).toHaveProperty("error");
      expect((result as { error: string }).error).toMatch(/delivered but death unconfirmed/);
      expect((result as { error: string }).error).toContain("424242");
    });
  });

  test("grandchild receipts above the largest process id send nothing", async () => {
    const sent: unknown[] = [];
    await withReceipt("2147483648", async (file) => {
      expect(await settle(() => killGrandchild(file, (...args) => sent.push(args), gone, 200))).toHaveProperty("error");
    });
    expect(sent).toEqual([]);
    await withReceipt("2147483647", async (file) => {
      expect(await settle(() => killGrandchild(file, (...args) => sent.push(args), gone, 200))).toEqual({ value: "dead" });
    });
    expect(sent).toEqual([[2147483647, "SIGKILL"]]);
  });

  test("liveness counts a process it may not signal as present and rejects other probe errors", () => {
    expect(alive(2147483646, gone)).toBe(false);
    expect(alive(2147483646, () => { throw errno("EPERM"); })).toBe(true);
    expect(() => alive(2147483646, () => { throw errno("EINVAL"); })).toThrow(/EINVAL/);
  });

  test("an invalid receipt still removes the receipt dir", async () => {
    let receipts = "";
    const sent: unknown[] = [];
    const outcome = await withGrandchild(async ({ pidFile }) => {
      receipts = dirname(pidFile);
      writeFileSync(pidFile, "0");
    }, { send: (...args) => sent.push(args), probe: gone }).then(() => "released", (error: Error) => error);
    expect(outcome).toBeInstanceOf(Error);
    expect(sent).toEqual([]);
    expect(existsSync(receipts)).toBe(false);
  });

  // The whole acquisition, setup, body and cleanup matrix for withGrandchild, with recorders and
  // injected failures. "424242" is a recorded grandchild that is only ever recorded, never sent.
  const messagesOf = (error: unknown) => (error instanceof AggregateError ? error.errors : [error]).map((item) => (item as Error).message);
  type Case = { body?: "fails"; stop?: "rejects"; send?: "EPERM"; probe?: "EINVAL"; rm?: "fails" };
  async function grandchildCase(failures: Case) {
    const steps: string[] = [];
    let receipts = "";
    const fakeServer = { stop: () => { steps.push("stop"); return failures.stop ? Promise.reject(new Error("stop failed")) : Promise.resolve(); } } as unknown as LegacyServer;
    const outcome = await withGrandchild(async ({ pidFile, start }) => {
      await start(["human"]);
      writeFileSync(pidFile, "424242");
      if (failures.body) throw new Error("body assertion failed");
      return "done";
    }, {
      mkdtemp: (prefix) => (receipts = mkdtempSync(prefix)),
      start: (async () => fakeServer) as unknown as typeof startLegacyServer,
      send: (pid, signal) => { steps.push(`send ${pid} ${signal}`); if (failures.send) throw errno(failures.send); },
      probe: (pid) => { steps.push(`probe ${pid}`); throw errno(failures.probe ?? "ESRCH"); },
      rm: (dir) => { steps.push("rm"); if (failures.rm) throw new Error("rm failed"); rmSync(dir, { recursive: true, force: true }); },
      boundMs: 200,
    }).then((value) => ({ value, errors: [] as string[] }), (error: unknown) => ({ value: undefined, errors: messagesOf(error) }));
    const receiptsGone = !existsSync(receipts);
    rmSync(receipts, { recursive: true, force: true });
    return { ...outcome, steps, receiptsGone };
  }
  const killed = ["send 424242 SIGKILL", "probe 424242"];

  test("withGrandchild: a clean run stops the server, confirms the grandchild dead and removes the receipts", async () => {
    expect(await grandchildCase({})).toEqual({ value: "done", errors: [], steps: ["stop", ...killed, "rm"], receiptsGone: true });
  });

  test("withGrandchild: a failed acquisition reports its cause and attempts nothing", async () => {
    const steps: string[] = [];
    const outcome = await withGrandchild(async () => { steps.push("body"); }, {
      mkdtemp: () => { throw new Error("mkdtemp failed"); },
      send: () => steps.push("send"), probe: () => steps.push("probe"), rm: () => steps.push("rm"),
    }).then(() => [], messagesOf);
    expect(outcome).toEqual(["mkdtemp failed"]);
    expect(steps).toEqual([]);
  });

  test("withGrandchild: a body assertion failure stays primary after a full cleanup", async () => {
    expect(await grandchildCase({ body: "fails" })).toEqual({ value: undefined, errors: ["body assertion failed"], steps: ["stop", ...killed, "rm"], receiptsGone: true });
  });

  const cleanupFailures: [string, Case, string[], RegExp][] = [
    ["a kill that fails with EPERM", { send: "EPERM" }, ["stop", "send 424242 SIGKILL", "rm"], /^Could not kill grandchild 424242 from receipt .*grandchild\.pid: EPERM$/],
    ["a probe that fails with EINVAL", { probe: "EINVAL" }, ["stop", ...killed, "rm"], /^Could not probe grandchild 424242 from receipt .*grandchild\.pid: .*EINVAL/],
    ["a failed receipts removal", { rm: "fails" }, ["stop", ...killed, "rm"], /^rm failed$/],
    ["a server stop that rejects", { stop: "rejects" }, ["stop", ...killed, "rm"], /^stop failed$/],
  ];
  for (const [name, failure, steps, cause] of cleanupFailures) {
    test(`withGrandchild: ${name} is reported and every other cleanup step still runs`, async () => {
      const result = await grandchildCase(failure);
      expect(result.steps).toEqual(steps);
      expect(result.errors).toHaveLength(1);
      expect(result.errors[0]).toMatch(cause);
      expect(result.receiptsGone).toBe(!failure.rm);
    });
    test(`withGrandchild: ${name} after a body failure keeps both causes, the body's first`, async () => {
      const result = await grandchildCase({ ...failure, body: "fails" });
      expect(result.steps).toEqual(steps);
      expect(result.errors).toHaveLength(2);
      expect(result.errors[0]).toBe("body assertion failed");
      expect(result.errors[1]).toMatch(cause);
      expect(result.receiptsGone).toBe(!failure.rm);
    });
  }

  for (const recorded of [true, false]) {
    test(`withGrandchild: a server that exits during startup ${recorded ? "with" : "without"} a grandchild keeps the startup error primary`, async () => {
      const sent: number[] = [];
      let receipts = "";
      const exits = `${recorded ? grandchild : ""} console.error("fake pid " + process.pid); process.exit(0);`;
      const outcome = await withGrandchild(async ({ pidFile, start }) => {
        receipts = dirname(pidFile);
        await start(["human"], { command: () => [node, ["-e", exits, pidFile]], stopTimeoutMs: 1000 });
      }, { send: (pid, signal) => { sent.push(pid); process.kill(pid, signal); } }).then(() => [], messagesOf);
      expect(outcome).toHaveLength(1);
      expect(outcome[0]).toMatch(/^The legacy server exited/);
      expect(existsSync(receipts)).toBe(false);
      expect(sent).toHaveLength(recorded ? 1 : 0);
      if (recorded) expect(alive(sent[0])).toBe(false);
    });
  }
});
