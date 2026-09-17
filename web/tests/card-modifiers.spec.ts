import { test, expect } from "@playwright/test";
import { cardLabel } from "../src/cardPresentation";
import { jokerChanges } from "../src/decisionPresentation";

test("modifier labels distinguish editions, sticker counts, false flags and concealment", () => {
  expect(cardLabel("Drunkard", ["edition: POLYCHROME", "eternal: True"])).toBe(
    "Drunkard (P · ∞)",
  );
  expect(
    cardLabel("Drunkard", ["edition: FOIL", "perishable: 3", "rental: True"]),
  ).toBe("Drunkard (F · X3 · R)");
  expect(
    cardLabel("Joker", ["edition: HOLO", "perishable: 0", "Debuffed"]),
  ).toBe("Joker (H · X0 · D)");
  expect(
    cardLabel("Joker", [
      "edition: NEGATIVE",
      "eternal: False",
      "rental: False",
    ]),
  ).toBe("Joker (N)");
  expect(
    cardLabel("Joker", ["enhancement: DISCARD SIZE", "rental: False"]),
  ).toBe("Joker");
  expect(cardLabel("PRIVATE_IDENTITY", ["edition: POLYCHROME"], true)).toBe(
    "Hidden",
  );
  expect(
    jokerChanges(
      {
        event_id: "a",
        decision: 1,
        ante: 1,
        phase: "SHOP",
        note: null,
        type: "use_consumable",
        item: "Drunkard",
        effects: ["edition: POLYCHROME"],
        jokers_added: ["Drunkard", "Drunkard"],
      },
      "added",
    ),
  ).toEqual(["Drunkard", "Drunkard"]);
});

test("explorer shows pickup modifiers in live rows, details and the board on a phone", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const { operator_token } = await (
    await page.request.get("/api/bootstrap")
  ).json();
  const headers = { "X-BH-Operator": operator_token };
  const created = await page.request.post("/api/runs", {
    headers,
    data: { agent: "heuristic", offline: true },
  });
  expect(created.ok()).toBe(true);
  const { episode_id } = await created.json();
  await expect
    .poll(async () => {
      const status = await (
        await page.request.get("/api/operator/status", { headers })
      ).json();
      return status.episodes.find((r: any) => r.episode_id === episode_id)
        ?.summary?.outcome;
    })
    .toBe("WIN");
  await page.route("**/api/review/decisions", async (route) => {
    const response = await route.fetch();
    const ledger = await response.json();
    for (const row of ledger.actions)
      if (row.type === "buy") {
        row.item = "Drunkard";
        row.effects = [
          "+1 discard each round",
          "edition: POLYCHROME",
          "eternal: True",
        ];
        row.jokers_added = ["Drunkard"];
      }
    ledger.summary = null;
    await route.fulfill({ response, json: ledger });
  });
  await page.route("**/api/review/decisions/3", async (route) => {
    const response = await route.fetch();
    const view = await response.json();
    view.transition.state.jokers = [
      {
        id: "recorded-drunkard",
        label: "Drunkard",
        face_down: false,
        rank: null,
        suit: null,
        counters: {},
        sellable: false,
        usable: false,
        min_targets: 0,
        max_targets: 0,
        effects: [
          "+1 discard each round",
          "edition: POLYCHROME",
          "eternal: True",
        ],
      },
    ];
    await route.fulfill({ response, json: view });
  });
  await page.goto(`/#explore/${episode_id}`);
  const list = page.getByRole("region", { name: "Recorded choices" });
  const purchase = list.getByRole("button", { name: /Buy Drunkard \(P · ∞\)/ });
  await expect(purchase).toContainText("+ Drunkard (P · ∞)");
  await purchase.click();
  const detail = page.getByRole("region", { name: "Decision details" });
  await expect(
    detail.getByRole("heading", { name: "Buy Drunkard (P · ∞)", exact: true }),
  ).toBeVisible();
  await expect(
    detail.getByText("Added: Drunkard (P · ∞)", { exact: true }),
  ).toBeVisible();
  await detail
    .getByRole("button", { name: "After decision", exact: true })
    .click();
  await expect(
    detail.locator(".owned strong", { hasText: "Drunkard (P · ∞)" }),
  ).toBeVisible();
  await detail.getByText("Card modifier key", { exact: true }).click();
  await expect(detail.getByText(/X = perishable/)).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
