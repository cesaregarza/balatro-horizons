import { test, expect } from "@playwright/test";

test("review distinguishes skip offers from owned effects and escapes descriptions", async ({
  page,
}) => {
  // Stub only this browser's public review response; no game launch or trace edits.
  await page.route("**/api/reviews", async (route) => {
    const response = await route.fetch();
    if (!response.ok()) {
      await route.fulfill({ response });
      return;
    }
    const data = await response.json();
    const state = data.view.observation.state;
    state.revealed_blinds[0].status = "SELECT";
    state.revealed_blinds[0].disabled = true;
    state.revealed_blinds[0].skip_reward = {
      label: "Investment Tag",
      effects: ["After defeating the Boss Blind, gain $25"],
      acquisition_condition: "skip_this_blind",
    };
    state.owned_vouchers = [
      { label: "Seed Money", effects: ["Raise interest cap to $10"] },
    ];
    state.pending_tags = [
      {
        label: "Double Tag",
        effects: ['<img src=x onerror="window.injected=true">'],
      },
    ];
    await route.fulfill({ response, json: data });
  });
  await page.goto("/");
  await page.getByRole("button", { name: /Start test episode/ }).click();
  await expect(page.getByRole("status")).toContainText("Run created");
  await expect(async () => {
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await page.getByRole("button", { name: "Review →" }).first().click();
    await expect(
      page.getByRole("heading", { name: "What was knowable here?" }),
    ).toBeVisible();
  }).toPass({ timeout: 10000 });
  await page
    .getByText("Blind previews & persistent effects", { exact: true })
    .click();
  await expect(page.getByText(/Offered on skip:/)).toContainText(
    "Investment Tag",
  );
  await expect(page.getByText(/Effects disabled/)).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Owned vouchers" }),
  ).toContainText("Raise interest cap to $10");
  const pending = page.getByRole("region", { name: "Pending tags" });
  await expect(pending).toContainText("Double Tag");
  await expect(pending).not.toContainText("Investment Tag");
  await expect(pending.locator("img")).toHaveCount(0);
  expect(await page.evaluate(() => Object.hasOwn(window, "injected"))).toBe(
    false,
  );
});
