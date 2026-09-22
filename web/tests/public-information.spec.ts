import { test, expect } from "@playwright/test";
import { awaitIdleWorker } from "./runHelpers";

function addPublicInformation(state: any) {
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
}

test("review distinguishes skip offers from owned effects and escapes descriptions", async ({
  page,
}) => {
  // Stub only this browser's public explorer response; no game launch or trace edits.
  await page.route("**/api/explore/sessions", async (route) => {
    const response = await route.fetch();
    if (!response.ok()) {
      await route.fulfill({ response });
      return;
    }
    const data = await response.json();
    addPublicInformation(data.view.observation.state);
    await route.fulfill({ response, json: data });
  });
  await page.route(/\/api\/explore\/decisions\/\d+$/, async (route) => {
    const response = await route.fetch();
    if (!response.ok()) {
      await route.fulfill({ response });
      return;
    }
    const data = await response.json();
    addPublicInformation(data.observation.state);
    await route.fulfill({ response, json: data });
  });
  await page.goto("/");
  await awaitIdleWorker(page);
  const created = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/runs") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: /Start test episode/ }).click();
  const response = await created;
  expect(response.ok(), await response.text()).toBe(true);
  const { episode_id } = await response.json();
  await expect(page.getByRole("status")).toContainText("Run created");
  // Run creation precedes the worker's first observation; this fixture needs a completed run.
  await awaitIdleWorker(page);
  const newRun = page.getByRole("row").filter({
    has: page.getByText(episode_id.slice(0, 10), { exact: true }),
  });
  await expect(async () => {
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await newRun.getByRole("button", { name: "Explore decisions" }).click();
    await expect(
      page.getByRole("heading", { name: "Decision explorer" }),
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
