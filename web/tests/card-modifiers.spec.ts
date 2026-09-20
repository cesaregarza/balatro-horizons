import { test, expect } from "@playwright/test";
import {
  cardLabel,
  cardClasses,
  visualModifiers,
} from "../src/cardPresentation";
import { jokerChanges } from "../src/decisionPresentation";
import { awaitIdleWorker } from "./runHelpers";

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
    cardLabel("5 of Clubs", [
      "edition: FOIL",
      "enhancement: STEEL",
      "seal: RED",
    ]),
  ).toBe("5 of Clubs (F · Steel · Red seal)");
  expect(
    visualModifiers(
      ["edition: POLYCHROME", "enhancement: GLASS", "seal: BLUE"],
      true,
    ),
  ).toEqual([]);
  expect(
    cardClasses(["edition: user-supplied class", "enhancement: DISCARD SIZE"]),
  ).toBe("card-edition-unknown");
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
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const { operator_token } = await (
    await page.request.get("/api/bootstrap")
  ).json();
  const headers = { "X-BH-Operator": operator_token };
  await awaitIdleWorker(page, operator_token);
  const created = await page.request.post("/api/runs", {
    headers,
    data: { agent: "heuristic", offline: true },
  });
  expect(created.ok(), await created.text()).toBe(true);
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
    view.transition.state.hand = [
      {
        rank: "5",
        suit: "Clubs",
        enhancement: "STEEL",
        edition: "FOIL",
        seal: "RED",
      },
      {
        rank: "A",
        suit: "Hearts",
        enhancement: "GLASS",
        edition: "HOLO",
        seal: "BLUE",
      },
      {
        rank: "8",
        suit: "Spades",
        enhancement: "LUCKY",
        edition: "POLYCHROME",
        seal: "PURPLE",
      },
      {
        rank: "K",
        suit: "Diamonds",
        enhancement: "GOLD",
        edition: "NEGATIVE",
        seal: "GOLD",
      },
    ].map((c, i) => ({
      ...view.transition.state.jokers[0],
      id: `test-card-${i}`,
      label: `${c.rank} of ${c.suit}`,
      rank: c.rank,
      suit: c.suit,
      effects: [
        `edition: ${c.edition}`,
        `enhancement: ${c.enhancement}`,
        `seal: ${c.seal}`,
      ],
    }));
    view.transition.state.hand.push({
      ...view.transition.state.hand[0],
      id: "test-hidden",
      face_down: true,
      label: "PRIVATE_IDENTITY",
      effects: ["edition: POLYCHROME", "seal: GOLD", "PRIVATE_EFFECT"],
    });
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
    detail.locator(".owned strong .edition-polychrome", {
      hasText: "Drunkard",
    }),
  ).toBeVisible();
  const joker = detail.locator(".owned article.card-edition-polychrome");
  await expect(joker.getByText("Polychrome", { exact: true })).toBeVisible();
  await expect(joker.getByText("∞ Eternal", { exact: true })).toBeVisible();
  const steel = detail.getByRole("button", {
    name: "5 of Clubs (F · Steel · Red seal)",
    exact: true,
  });
  await expect(steel).toHaveClass(/card-enhancement-steel/);
  await expect(steel.locator(".seal-red")).toContainText("Red seal");
  for (const color of ["red", "blue", "purple", "gold"])
    await expect(detail.locator(`.playing-card .seal-${color}`)).toHaveCount(1);
  const hidden = detail.getByRole("button", { name: "Hidden", exact: true });
  await expect(hidden).toHaveAttribute("title", "Hidden card");
  await expect(hidden.locator(".modifier-badge")).toHaveCount(0);
  await expect(hidden).not.toHaveClass(/edition|enhancement|red/);
  expect(await hidden.evaluate((el) => el.outerHTML)).not.toContain("PRIVATE_");
  await detail.getByText("Card modifier key", { exact: true }).click();
  await expect(detail.getByText(/X = perishable/)).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await detail
    .locator(".board")
    .screenshot({ path: testInfo.outputPath("card-modifiers-phone.png") });
  await page.setViewportSize({ width: 1440, height: 1100 });
  await detail
    .locator(".board")
    .screenshot({ path: testInfo.outputPath("card-modifiers-desktop.png") });
});
