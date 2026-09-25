import { test, expect, type Page } from "@playwright/test";

const episodeId = "a".repeat(32);
const reviewToken = "mock-review";

function makeView() {
  return {
    episode_id: episodeId,
    decision: 4,
    stage: "transition" as const,
    observation: {
      episode_id: episodeId,
      observation_id: 4,
      phase: "blind_select",
      memory: "",
      available_action_types: ["skip_blind"],
      action_constraints: { reorder: { areas: [] } },
      remaining_budget: {},
      state: {
        progress: { ante: 2, blind: "Small Blind" },
        resources: { money: 5, hands: 4, discards: 3, chips: 0, target: 300 },
        hand: [],
        jokers: [],
        consumables: [],
        offers: [],
        revealed_blinds: [{ id: "blind-id", label: "Small Blind", target: "300", skip_allowed: true, effects: [] }],
        hand_levels: {},
        persistent_effects: [],
      },
    },
    evidence_kind: "NATIVE",
    evaluation_eligible: true,
    fixture: null,
    can_advance: true,
    review_mode: "retrospective",
    exposure: {},
    trajectory: [],
  };
}

async function openReview(page: Page, options: {
  requiresUncapped?: boolean;
  paidGateError?: boolean;
  delayBranch?: boolean;
} = {}) {
  const branches: unknown[] = [];
  let releaseBranch = () => {};
  const branchGate = new Promise<void>((resolve) => { releaseBranch = resolve; });
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/bootstrap") {
      return route.fulfill({ json: {
        operator_token: "mock-operator",
        config: { workbench: true, models: {}, model_capabilities: {}, budgets: { paid_calls_enabled: false } },
        workbench: true,
        paid_credentials: {},
      } });
    }
    if (path === "/api/episodes") {
      return route.fulfill({ json: [{
        episode_id: episodeId,
        created_at: "2026-09-24T00:00:00Z",
        evidence_kind: "NATIVE",
        deck: "Red",
        stake: "Gold",
        branch: false,
        evaluation_eligible: true,
        fixture: null,
        agent: "fixture",
      }] });
    }
    if (path === "/api/panels" || path === "/api/batches" || path === "/api/review/annotations") {
      return route.fulfill({ json: [] });
    }
    if (path === "/api/operator/status") {
      return route.fulfill({ json: { running: false, active_episode: null, episodes: [], error: null } });
    }
    if (path === "/api/reviews" && request.method() === "POST") {
      return route.fulfill({ json: { review_token: reviewToken, view: makeView() } });
    }
    if (path === "/api/review/branch-capability") {
      return route.fulfill({ json: {
        enabled: true,
        reason: null,
        requires_uncapped_confirmation: options.requiresUncapped ?? false,
      } });
    }
    if (path === "/api/branches" && request.method() === "POST") {
      branches.push(request.postDataJSON());
      if (options.delayBranch) await branchGate;
      if (options.paidGateError) return route.fulfill({ status: 400, json: { error: "PAID_EXECUTION_NOT_AUTHORIZED" } });
      return route.fulfill({ json: { episode_id: "b".repeat(32) } });
    }
    if (path === "/api/operator/human") return route.fulfill({ json: { waiting: false } });
    return route.fulfill({ status: 200, json: {} });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Review →" }).click();
  await expect(page.getByRole("heading", { name: "What was knowable here?" })).toBeVisible();
  await page.getByRole("button", { name: "Explore an alternative" }).click();
  return { branches, releaseBranch };
}

test("uncapped warning is red and each mode needs its own confirmation", async ({ page }) => {
  const { branches } = await openReview(page, { requiresUncapped: true });
  const warning = page.getByRole("alert").filter({ hasText: "Uncapped cost limit applies" });
  await expect(warning).toHaveCSS("color", "rgb(240, 120, 130)");
  await expect(warning).toContainText("including human takeover and action overrides");

  let confirmations = 0;
  page.on("dialog", async (dialog) => {
    confirmations += 1;
    expect(dialog.message()).toMatch(/^Are you sure\?/);
    expect(dialog.message()).toContain("inherits an Uncapped dollar limit");
    if (confirmations === 1) await dialog.dismiss();
    else await dialog.accept();
  });
  await page.getByRole("button", { name: "Resume agent", exact: true }).click();
  expect(branches).toEqual([]);
  await page.getByRole("button", { name: "Take over" }).click();
  await expect(page.getByRole("heading", { name: "Human control" })).toBeVisible();
  expect(confirmations).toBe(2);
  expect(branches).toEqual([{
    episode_id: episodeId,
    decision: 4,
    mode: "human_takeover",
    actions: [],
    confirm_uncapped: true,
  }]);
});

test("uncapped action override is confirmed and carries the capability flag", async ({ page }) => {
  const { branches } = await openReview(page, { requiresUncapped: true });
  page.once("dialog", (dialog) => { void dialog.accept(); });
  await page.getByRole("button", { name: "Skip blind" }).click();
  await expect(page.getByRole("heading", { name: "Human control" })).toBeVisible();
  expect(branches).toEqual([{
    episode_id: episodeId,
    decision: 4,
    mode: "single_action_override",
    actions: [{ type: "skip_blind", blind_id: "blind-id" }],
    confirm_uncapped: true,
  }]);
});

test("finite branch preserves its old payload without confirmation flag", async ({ page }) => {
  const { branches } = await openReview(page);
  await page.getByRole("button", { name: "Resume agent", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Human control" })).toBeVisible();
  expect(branches).toEqual([{
    episode_id: episodeId,
    decision: 4,
    mode: "agent_continue",
    actions: [],
  }]);
});

test("backend paid gate errors remain visible after uncapped confirmation", async ({ page }) => {
  const { branches } = await openReview(page, { requiresUncapped: true, paidGateError: true });
  page.once("dialog", (dialog) => { void dialog.accept(); });
  await page.getByRole("button", { name: "Resume agent", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "PAID_EXECUTION_NOT_AUTHORIZED" })).toBeVisible();
  expect(branches).toHaveLength(1);
});

test("branch lock prevents duplicate Board actions and disables controls in flight", async ({ page }) => {
  const { branches, releaseBranch } = await openReview(page, { delayBranch: true });
  await page.getByRole("button", { name: "Skip blind" }).evaluate((element) => {
    (element as HTMLButtonElement).click();
    (element as HTMLButtonElement).click();
  });
  await expect.poll(() => branches.length).toBe(1);
  await expect(page.getByRole("button", { name: "Resume agent", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Skip blind" })).toBeDisabled();
  expect(branches).toHaveLength(1);
  releaseBranch();
  await expect(page.getByRole("heading", { name: "Human control" })).toBeVisible();
  expect(branches).toHaveLength(1);
});
