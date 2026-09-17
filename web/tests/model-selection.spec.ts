import { test, expect } from "@playwright/test";
import {
  configureModel,
  effortOptions,
  harnessLabel,
  modelLabel,
  modelCatalog,
  modelKey,
  type ModelConfig,
} from "../src/modelSelection";

const terra: ModelConfig = {
  provider: "openai",
  model: "gpt-5.6-terra",
  input_usd_per_million: 2,
  output_usd_per_million: 12,
  cached_input_usd_per_million: 0.2,
  cache_write_input_usd_per_million: 2.5,
  pricing_date: "2026-09-15",
  settings: {
    harness_interface: "tools_v4",
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
  settings: { harness_interface: "tools_v3", reasoning_effort: "medium" },
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

test("Sol and Astra expose their supported reasoning choices", () => {
  expect(modelLabel(sol)).toBe("GPT-5.6 Sol · OpenAI");
  expect(modelLabel(astra)).toBe("GPT-6 Astra · OpenAI");
  expect(effortOptions(sol)).toContain("none");
  expect(effortOptions(astra)).toEqual([
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
  ]);
  expect(() => configureModel(astra, "none", "tools_v7")).toThrow();
  expect(
    configureModel(astra, "max", "tools_v7").settings.reasoning_effort,
  ).toBe("max");
  expect(
    configureModel(astra, "max", "tools_v7").settings.harness_interface,
  ).toBe("tools_v7");
});

test("model defaults deduplicate aliases without rewriting historical settings", () => {
  const legacy = {
    ...terra,
    settings: { harness_interface: "tools_v3", reasoning_effort: "medium" },
  };
  const v5Alias = {
    ...terra,
    settings: { ...terra.settings, harness_interface: "tools_v5" },
  };
  const v7Alias = configureModel(terra, "medium", "tools_v7");
  const models = {
    "terra-v7": v7Alias,
    "terra-v5": v5Alias,
    "terra-tools": legacy,
    "luna-tools": luna,
  };
  expect(Object.keys(modelCatalog(models))).toHaveLength(2);
  expect(modelCatalog(models)[modelKey(terra)]).toEqual(v7Alias);
  expect(
    modelCatalog({ "terra-v5": v5Alias, "terra-tools": legacy })[
      modelKey(terra)
    ],
  ).toEqual(v5Alias);
  expect(harnessLabel(v7Alias)).toBe("Current harness (v7)");
  expect(harnessLabel(v5Alias)).toBe("Legacy harness (tools_v5)");
  const selected = configureModel(terra, "high", "tools_v7");
  expect(
    modelCatalog({ ...models, [modelKey(terra)]: selected })[modelKey(terra)],
  ).toEqual(selected);
  expect(terra.settings.reasoning_effort).toBe("medium");
  expect(selected.cached_input_usd_per_million).toBe(0.2);
  for (const retired of [
    "operate_v1",
    "tools_v2",
    "tools_v3",
    "tools_v4",
    "tools_v5",
  ])
    expect(() => configureModel(terra, "medium", retired)).toThrow(
      "Legacy harnesses",
    );
  expect(() => configureModel(terra, "minimal", "tools_v7")).toThrow();
  expect(() => configureModel(luna, "medium", "tools_v7")).toThrow();
  const anthropic: ModelConfig = {
    ...luna,
    provider: "anthropic",
    model: "pinned-anthropic",
    settings: { thinking_budget: 2048 },
  };
  expect(configureModel(anthropic, "", "tools_v7").settings).toEqual({
    thinking_budget: 2048,
    harness_interface: "tools_v7",
  });
});

test("model, effort and harness persist; launching preserves fresh budgets and exact settings", async ({
  page,
}) => {
  const bootstrap = await (await page.request.get("/api/bootstrap")).json();
  const headers = { "X-BH-Operator": bootstrap.operator_token };
  const original = bootstrap.config;
  const models = {
    sol,
    astra,
    luna: luna,
    "luna-tools": luna,
    "terra-tools": {
      ...terra,
      settings: { ...terra.settings, harness_interface: "tools_v3" },
    },
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
      await expect(page.getByLabel("Harness", { exact: true })).toHaveValue(
        "tools_v7",
      );
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
    await expect(page.getByLabel("Harness", { exact: true })).toHaveValue(
      "tools_v7",
    );
    await expect(
      page.getByLabel("Harness", { exact: true }).locator("option"),
    ).toHaveText(["Current harness (v7)"]);
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
    await expect(page.getByLabel("Harness", { exact: true })).toHaveValue(
      "tools_v7",
    );
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
    await page.getByRole("button", { name: /Start test episode/ }).click();
    await expect(page.getByRole("status")).toContainText("Run created");
    expect(launches).toEqual([
      { agent: modelKey(terra), offline: true, preset: "pilot", seed: null },
    ]);
    const saved = (await (await page.request.get("/api/bootstrap")).json())
      .config;
    expect(saved.budgets).toEqual(budgets);
    expect(saved.skills).toBe("none");
    expect(saved.models[modelKey(terra)].settings).toEqual({
      ...terra.settings,
      harness_interface: "tools_v7",
      reasoning_effort: "max",
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
    // A model still saved on v4 must also freeze new plans on v7.
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
    expect(
      plannedSettings.models[modelKey(sol)].settings.harness_interface,
    ).toBe("tools_v7");
    expect(plannedSettings.models.sol).toEqual(sol);

    await page
      .getByRole("button", { name: "Models & budgets", exact: true })
      .click();
    await page
      .getByText("GPT-6 Astra · OpenAI", { exact: true })
      .locator("..")
      .getByRole("button", { name: "Edit connection" })
      .click();
    await expect(page.getByLabel("Harness", { exact: true })).toHaveValue(
      "tools_v7",
    );
    await expect(
      page.getByLabel("Harness", { exact: true }).locator("option"),
    ).toHaveText(["Current harness (v7)"]);
    // Legacy values in the JSON field cannot reintroduce a retired selection.
    await page
      .getByLabel("Additional model settings (JSON)")
      .fill(JSON.stringify(astra.settings));
    await page.getByRole("button", { name: "Save model", exact: true }).click();
    await expect(page.getByRole("status")).toHaveText(
      "Model configuration saved.",
    );
    const edited = (await (await page.request.get("/api/bootstrap")).json())
      .config;
    expect(edited.models[modelKey(astra)].settings.harness_interface).toBe(
      "tools_v7",
    );
    expect(edited.models.astra).toEqual(astra);
    expect(launches).toHaveLength(1);
    await page.getByRole("button", { name: "Runs", exact: true }).click();
    await picker.selectOption(modelKey(luna));
    await expect(
      page.getByLabel("Harness", { exact: true }).locator("option"),
    ).toHaveCount(1);
    await expect(
      page
        .getByLabel("Harness", { exact: true })
        .locator('option[value="tools_v7"]'),
    ).toHaveAttribute("disabled", "");
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

test("a settings rejection prevents a run from starting", async ({ page }) => {
  await page.route("**/api/bootstrap", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.config.models = { "terra-cache": terra };
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
  await page.getByRole("button", { name: /Start test episode/ }).click();
  await expect(page.getByRole("alert")).toContainText("WORKER_BUSY");
  expect(launches).toBe(0);
});
