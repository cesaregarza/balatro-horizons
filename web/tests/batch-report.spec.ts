import { test, expect } from "@playwright/test";

const batchId = "b".repeat(32);

test("batch report separates game losses from non-game endings", async ({ page }) => {
  await page.route("**/api/batches", (route) => route.fulfill({ json: [{
    batch_id: batchId, slots: [{}, {}, {}, {}, {}], config: { deck: "RED", stake: "GOLD" }, split: "development",
  }] }));
  await page.route(`**/api/batches/${batchId}/report`, (route) => route.fulfill({ json: {
    evidence_kinds: ["SYNTHETIC_TEST"],
    agents: { heuristic: {
      planned: 5, valid: 4, wins: 1, game_losses: 1, unresolved: 1,
      non_game_endings: { BUDGET_EXHAUSTED: 1, AGENT_PROTOCOL_FAILURE: 1 },
      missing_outcome_bounds: [0.2, 0.4], all_attempt_cost_usd: 0,
    } },
  } }));
  await page.goto("/");
  await page.getByRole("button", { name: "Batches & reports", exact: true }).click();
  await page.getByRole("button", { name: "Reveal report", exact: true }).click();
  const table = page.getByRole("table");
  await expect(table.getByRole("columnheader", { name: "Game losses", exact: true })).toBeVisible();
  await expect(table.getByRole("columnheader", { name: "Non-game endings", exact: true })).toBeVisible();
  const row = table.getByRole("row").filter({ hasText: "heuristic" });
  await expect(row.getByRole("cell").nth(1)).toHaveText("1 / 4");
  await expect(row.getByRole("cell").nth(2)).toHaveText("1");
  await expect(row.getByRole("cell").nth(3)).toHaveText("2 (BUDGET_EXHAUSTED 1, AGENT_PROTOCOL_FAILURE 1)");
});
