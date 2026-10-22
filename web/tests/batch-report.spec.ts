import { test, expect } from "@playwright/test";

const batchId = "b".repeat(32);

test("batch report separates game losses, valid non-game endings and all-attempt terminals", async ({ page }) => {
  await page.route("**/api/batches", (route) => route.fulfill({ json: [{
    batch_id: batchId, slots: [{}, {}, {}, {}, {}], config: { deck: "RED", stake: "GOLD" }, split: "development",
  }] }));
  await page.route(`**/api/batches/${batchId}/report`, (route) => route.fulfill({ json: {
    evidence_kinds: ["SYNTHETIC_TEST"],
    agents: { heuristic: {
      planned: 5, valid: 4, wins: 1, game_losses: 1, unresolved: 1,
      non_game_endings: { BUDGET_EXHAUSTED: 1, AGENT_PROTOCOL_FAILURE: 1 },
      non_game_attempts: { BUDGET_EXHAUSTED: 1, AGENT_PROTOCOL_FAILURE: 1, INFRASTRUCTURE_FAILURE: 2 },
      missing_outcome_bounds: [0.2, 0.4], all_attempt_cost_usd: 0,
    } },
  } }));
  await page.goto("/");
  await page.getByRole("button", { name: "Batches & reports", exact: true }).click();
  await page.getByRole("button", { name: "Reveal report", exact: true }).click();
  const table = page.getByRole("table");
  const headers = ["Agent", "Wins / valid", "Game losses", "Non-game endings (valid slots)",
    "Non-game terminals (all attempts)", "Coverage"];
  await expect(table.getByRole("columnheader")).toContainText(headers);
  const row = table.getByRole("row").filter({ hasText: "heuristic" });
  await expect(row.getByRole("cell")).toContainText([
    "heuristic", "1 / 4", "1", "2 (BUDGET_EXHAUSTED 1, AGENT_PROTOCOL_FAILURE 1)",
    "4 (BUDGET_EXHAUSTED 1, AGENT_PROTOCOL_FAILURE 1, INFRASTRUCTURE_FAILURE 2)", "4 / 5",
  ]);
});
