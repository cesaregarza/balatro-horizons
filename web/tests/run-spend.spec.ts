import { test, expect, type Page } from "@playwright/test";
import type { DecisionLedger, RunSpendTotals } from "../src/api/client";

const eid = "f".repeat(32);

async function mockRun(page: Page, spend?: RunSpendTotals) {
  const state = {
    fail: false, polls: 0,
    ledger: {
      source_journal_head: "spend-fixture", spend, summary: null,
      manifest: { episode_id: eid, agent: "fixture", evidence_kind: "SYNTHETIC_TEST", evaluation_eligible: false },
      actions: [], uncommitted_actions: [], rounds: [],
    } as DecisionLedger,
  };
  await page.route("**/api/explore/sessions", (route) => route.fulfill({ json: { review_token: "spend-fixture", view: null } }));
  await page.route("**/api/explore/decisions", (route) => {
    state.polls += 1;
    return route.fulfill(state.fail ? { status: 503, json: { error: "TEST_CONNECTION_INTERRUPTED" } } : { json: state.ledger });
  });
  return state;
}

for (const viewport of [{ width: 1440, height: 1100 }, { width: 390, height: 844 }]) {
  test(`run spend leads the explorer at ${viewport.width}px, before any game action`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const failures: string[] = [];
    page.on("pageerror", (error) => failures.push(error.message));
    await mockRun(page, { accounted_usd: 0.2345, response_usd: 0.0345, reserved_usd: 0.2 });
    await page.goto(`/#explore/${eid}`);
    const spend = page.getByRole("region", { name: "Run API spend" });
    const total = spend.locator(".run-spend-total");
    await expect(total).toHaveText("$0.2345");
    await expect(total).toBeInViewport();
    await expect(spend).toContainText("Live cost · refreshed every 2 seconds");
    await expect(spend).toContainText("$0.0345 recorded responses · $0.20 reserved for pending / unknown usage");
    await expect(spend).toContainText("not a provider invoice or in-game cash");
    await expect(spend).toContainText("Response costs may retain a conservative estimate");
    const box = (await spend.boundingBox())!;
    const controls = (await page.getByLabel("Decision exports").boundingBox())!;
    expect(box.y + box.height).toBeLessThan(controls.y);
    expect(await total.evaluate((node) => parseFloat(getComputedStyle(node).fontSize))).toBeGreaterThanOrEqual(30);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: test.info().outputPath("run-spend.png"), fullPage: true });
    expect(failures).toEqual([]);
  });
}

test("spend refreshes through reservation, settlement, pause, interruption and terminal", async ({ page }) => {
  await page.clock.install();
  const state = await mockRun(page, { accounted_usd: 0, response_usd: 0, reserved_usd: 0 });
  await page.goto(`/#explore/${eid}`);
  const spend = page.getByRole("region", { name: "Run API spend" });
  const total = spend.locator(".run-spend-total");
  await expect(total).toHaveText("$0.00");
  state.ledger.spend = { accounted_usd: 0.2, response_usd: 0, reserved_usd: 0.2 };
  await page.clock.runFor(2200);
  await expect(total).toHaveText("$0.20");
  state.ledger.spend = { accounted_usd: 0.00002, response_usd: 0.00002, reserved_usd: 0 };
  await page.clock.runFor(2200);
  await expect(total).toHaveText("<$0.0001");
  await page.getByLabel("Update live", { exact: true }).uncheck();
  await expect(spend).toContainText("live updates paused");
  await expect(page.getByRole("button", { name: "Refresh decisions" })).toBeEnabled();
  const pausedPolls = state.polls;
  await page.clock.runFor(6000);
  expect(state.polls).toBe(pausedPolls);
  state.fail = true;
  await page.getByLabel("Update live", { exact: true }).check();
  await expect(spend).toContainText("Last recorded total · connection interrupted");
  await expect(total).toHaveText("<$0.0001");
  state.fail = false;
  state.ledger.summary = { outcome: "WIN", cost_usd: 1.25 };
  state.ledger.spend = { accounted_usd: 1.25, response_usd: 1.05, reserved_usd: 0.2 };
  await page.clock.runFor(5200);
  await expect(total).toHaveText("$1.25");
  await expect(spend).toContainText("Final recorded total");
  await expect(spend).toContainText("$0.20 reserved for pending / unknown usage");
  const finalPolls = state.polls;
  await page.clock.runFor(6000);
  expect(state.polls).toBe(finalPolls);
});

test("old terminal totals survive missing breakdowns and unknown costs never become zero", async ({ page }) => {
  const state = await mockRun(page);
  state.ledger.summary = { outcome: "WIN", cost_usd: 12.3456 };
  await page.goto(`/#explore/${eid}`);
  const spend = page.getByRole("region", { name: "Run API spend" });
  await expect(spend.locator(".run-spend-total")).toHaveText("$12.3456");
  await expect(spend).toContainText("Reservation breakdown unavailable");
  state.ledger.summary = null;
  state.ledger.spend = { accounted_usd: null, response_usd: null, reserved_usd: null };
  await page.getByRole("button", { name: "Refresh decisions" }).click();
  await expect(spend.locator(".run-spend-total")).toHaveText("Unavailable");
});

test("live-status spend remains opt-in and prominent above action progress", async ({ page }) => {
  let polls = 0;
  await page.route("**/api/operator/status", (route) => {
    polls += 1;
    return route.fulfill({ json: {
      running: true, active_episode: eid, episodes: [{ episode_id: eid, agent: "fixture",
        summary: null, progress: { phase: "SHOP", committed_actions: 7, cost_usd: 0.23 },
        spend: { accounted_usd: 0.23, response_usd: 0.03, reserved_usd: 0.2 },
      }],
    } });
  });
  await page.goto("/");
  const watch = page.getByRole("button", { name: "Watch live status", exact: true });
  await expect(watch).toBeVisible();
  expect(polls).toBe(0);
  await expect(page.getByRole("region", { name: "Run API spend" })).toHaveCount(0);
  await watch.click();
  const spend = page.getByRole("region", { name: "Run API spend" });
  await expect(spend.locator(".run-spend-total")).toHaveText("$0.23");
  const box = (await spend.boundingBox())!;
  expect(box.y + box.height).toBeLessThan((await page.getByText("SHOP · 7 actions", { exact: true }).boundingBox())!.y);
  await page.getByRole("button", { name: "Hide live status", exact: true }).click();
  await expect(spend).toHaveCount(0);
});
