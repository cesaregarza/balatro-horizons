import { test, expect } from "@playwright/test";
import { modelKey, type ModelConfig } from "../src/modelSelection";
import { awaitIdleWorker } from "./runHelpers";

test("GPT-6 Sol and Luna expose and persist reasoning choices without starting runs", async ({
  page,
}) => {
  const bootstrap = await (await page.request.get("/api/bootstrap")).json();
  await awaitIdleWorker(page, bootstrap.operator_token);
  const headers = { "X-BH-Operator": bootstrap.operator_token };
  const original = bootstrap.config;
  const added = Object.fromEntries(
    ["sol", "luna"].map((tier) => {
      const sol = tier === "sol";
      const model: ModelConfig = {
        provider: "openai",
        model: `gpt-6-${tier}`,
        input_usd_per_million: sol ? 2 : 0.1,
        cached_input_usd_per_million: sol ? 0.2 : 0.01,
        cache_write_input_usd_per_million: sol ? 2.5 : 0.125,
        output_usd_per_million: sol ? 10 : 0.5,
        pricing_date: "2026-09-22",
        settings: { reasoning_effort: "medium", reasoning_summary: "auto" },
      };
      return [`${tier}6`, model];
    }),
  );
  const put = async (models: Record<string, ModelConfig>) => {
    const response = await page.request.put("/api/settings", {
      headers,
      data: { models, budgets: original.budgets, skills: original.skills },
    });
    expect(response.ok()).toBe(true);
  };
  const launches: unknown[] = [];
  await page.route("**/api/runs", async (route) => {
    launches.push(route.request().postDataJSON());
    await route.fulfill({ status: 409, json: { detail: "UNEXPECTED_RUN" } });
  });
  try {
    await put({ ...original.models, ...added });
    await page.goto("/");
    const picker = page.getByLabel("Model", { exact: true });
    const effort = page.getByLabel("Reasoning effort", { exact: true });
    for (const [alias, model] of Object.entries(added)) {
      await picker.selectOption(modelKey(model));
      await expect(picker.locator("option:checked")).toHaveText(
        alias === "sol6" ? "GPT-6 Sol · OpenAI" : "GPT-6 Luna · OpenAI",
      );
      await expect(effort).toHaveValue("medium");
      await expect(effort.locator("option")).toHaveText([
        "None", "Low", "Medium", "High", "Extra high", "Max",
      ]);
      const selected = alias === "sol6" ? "max" : "none";
      await effort.selectOption(selected);
      await page.getByRole("button", { name: "Save model defaults", exact: true }).click();
      await expect(page.getByRole("status")).toHaveText(
        "Model defaults saved. No run started.",
      );
      await page.reload();
      await picker.selectOption(modelKey(model));
      await expect(effort).toHaveValue(selected);
      const saved = (await (await page.request.get("/api/bootstrap")).json()).config;
      expect(saved.models[modelKey(model)]).toEqual({
        ...model, settings: { ...model.settings, reasoning_effort: selected },
      });
      expect(saved.budgets).toEqual(original.budgets);
      expect(saved.skills).toBe(original.skills);
      for (const [key, existing] of Object.entries(original.models))
        expect(saved.models[key]).toEqual(existing);
    }
    expect(launches).toHaveLength(0);
  } finally {
    await put(original.models);
  }
});
