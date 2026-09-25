import { test, expect, type Page } from "@playwright/test";

const episodeId = "a".repeat(32);
const parentHash = "b".repeat(64);
const planHash = "c".repeat(64);

function preview(source: "same_source" | "compatible_update" = "same_source", additionalAvailable = true, additionalReason: string | null = null) {
  return {
    episode_id: episodeId,
    available: true,
    reason: null,
    plan: {
      parent_terminal_hash: parentHash,
      plan_hash: planHash,
      decision: 8,
      accounted_usd: 7,
      additional_usd: 10 as const,
      additional_available: additionalAvailable,
      additional_reason: additionalReason,
      new_cap_usd: 17,
      source_compatibility: source,
    },
  };
}

async function openBudgetStop(page: Page, options: {
  source?: "same_source" | "compatible_update";
  workbench?: boolean;
  postStatus?: number;
  delayPost?: boolean;
  batchId?: string | null;
  parentEpisodeId?: string | null;
  reason?: string;
  additionalAvailable?: boolean;
  additionalReason?: string | null;
} = {}) {
  const posts: unknown[] = [];
  let previews = 0;
  let releasePost = () => {};
  const postGate = new Promise<void>((resolve) => { releasePost = resolve; });
  const workbench = options.workbench ?? true;
  const ledger = {
    source_journal_head: parentHash,
    manifest: {
      episode_id: episodeId,
      agent: "fixture",
      evidence_kind: "NATIVE",
      evaluation_eligible: false,
      parent_episode_id: options.parentEpisodeId,
      batch_id: options.batchId,
      config: { models: {}, model_capabilities: {} },
    },
    summary: { outcome: "INFRASTRUCTURE_FAILURE", reason: options.reason ?? "EPISODE_COST_CAP" },
    spend: { accounted_usd: 7, response_usd: 7, reserved_usd: 0 },
    actions: [],
    uncommitted_actions: [],
    rounds: [],
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/bootstrap") {
      return route.fulfill({ json: {
        operator_token: "mock-operator",
        config: { workbench, models: {}, model_capabilities: {}, budgets: {} },
        workbench,
        paid_credentials: {},
      } });
    }
    if (path === "/api/episodes" || path === "/api/panels" || path === "/api/batches") return route.fulfill({ json: [] });
    if (path === "/api/operator/status") {
      return route.fulfill({ json: { running: false, active_episode: null, episodes: [], error: null } });
    }
    if (path === "/api/explore/sessions" && request.method() === "POST") {
      return route.fulfill({ json: { review_token: "mock-review", view: null } });
    }
    if (path === "/api/explore/decisions") return route.fulfill({ json: ledger });
    if (path === `/api/operator/episodes/${episodeId}/continue-budget`) {
      if (request.method() === "POST") {
        posts.push(request.postDataJSON());
        if (options.delayPost) await postGate;
        if (options.postStatus && options.postStatus !== 200) {
          return route.fulfill({ status: options.postStatus, json: { error: "CONTINUATION_PLAN_CHANGED" } });
        }
        return route.fulfill({ json: { episode_id: "d".repeat(32) } });
      }
      previews += 1;
      return route.fulfill({ json: preview(options.source, options.additionalAvailable, options.additionalReason) });
    }
    return route.fulfill({ status: 200, json: {} });
  });
  await page.goto(`/#explore/${episodeId}`);
  await expect(page.getByRole("heading", { name: "Decision explorer" })).toBeVisible();
  return { posts, getPreviewCount: () => previews, releasePost };
}

test("preview is read-only and $10 adds an allowance without sending a combined cap", async ({ page }) => {
  const { posts, getPreviewCount } = await openBudgetStop(page);
  await page.getByRole("button", { name: "Review cost override" }).click();
  await expect(page.getByText("Recorded all-attempt spend: $7.00.")).toBeVisible();
  await expect(page.getByText("A $10 continuation allowance makes the new total ceiling $17.00.")).toBeVisible();
  await expect(page.getByText(/0 preliminary tests and 1 checked restore/)).toBeVisible();
  expect(getPreviewCount()).toBe(1);
  expect(posts).toEqual([]);

  await page.getByRole("button", { name: "$10 more" }).click();
  await page.getByLabel(/I authorize the paid continuation/).check();
  await page.getByRole("button", { name: "Add $10 and continue" }).click();
  await expect(page.getByRole("button", { name: "Hide live status" })).toBeVisible();
  expect(posts).toEqual([{
    parent_terminal_hash: parentHash,
    plan_hash: planHash,
    additional_cost_usd: 10,
    authorize_paid: true,
    accept_compatible_update: false,
  }]);
  expect(JSON.stringify(posts[0])).not.toContain("combined_cap_usd");
});

test("uncapped selection is red, cancel sends nothing, and acceptance sends explicit confirmation", async ({ page }) => {
  const { posts } = await openBudgetStop(page);
  await page.getByRole("button", { name: "Review cost override" }).click();
  const uncapped = page.getByRole("button", { name: "Uncapped" });
  await expect(uncapped).toHaveCSS("background-color", "rgb(142, 41, 50)");
  page.once("dialog", (dialog) => {
    expect(dialog.message()).toMatch(/^Are you sure\?/);
    expect(dialog.message()).toContain("no dollar ceiling");
    expect(dialog.message()).toMatch(/spending can keep growing/i);
    expect(dialog.message()).toContain("Call and action limits still apply");
    void dialog.dismiss();
  });
  await uncapped.click();
  await expect(page.getByRole("button", { name: "$10 more" })).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByRole("button", { name: "Add $10 and continue" })).toBeDisabled();
  expect(posts).toEqual([]);

  page.once("dialog", (dialog) => { void dialog.accept(); });
  await uncapped.click();
  await page.getByLabel(/I authorize the paid continuation/).check();
  await page.getByRole("button", { name: "Continue uncapped" }).click();
  await expect(page.getByRole("button", { name: "Hide live status" })).toBeVisible();
  expect(posts).toEqual([{
    parent_terminal_hash: parentHash,
    plan_hash: planHash,
    combined_cap_usd: "uncapped",
    authorize_paid: true,
    confirm_uncapped: true,
    accept_compatible_update: false,
  }]);
});

test("compatible source requires separate acceptance", async ({ page }) => {
  const { posts } = await openBudgetStop(page, { source: "compatible_update" });
  await page.getByRole("button", { name: "Review cost override" }).click();
  await expect(page.getByText(/Compatible code update:/)).toBeVisible();
  const submit = page.getByRole("button", { name: "Add $10 and continue" });
  await page.getByRole("button", { name: "$10 more" }).click();
  await page.getByLabel(/I authorize the paid continuation/).check();
  await expect(submit).toBeDisabled();
  await page.getByLabel(/I accept continuing with this compatible code update/).check();
  await submit.click();
  await expect(page.getByRole("button", { name: "Hide live status" })).toBeVisible();
  expect(posts[0]).toEqual({
    parent_terminal_hash: parentHash,
    plan_hash: planHash,
    additional_cost_usd: 10,
    authorize_paid: true,
    accept_compatible_update: true,
  });
});

test("unavailable $10 allowance stays disabled while confirmed Uncapped can proceed", async ({ page }) => {
  const { posts } = await openBudgetStop(page, {
    additionalAvailable: false,
    additionalReason: "No additional room under the finite episode limit.",
  });
  await page.getByRole("button", { name: "Review cost override" }).click();
  await expect(page.getByText("Additional $10 unavailable: No additional room under the finite episode limit.")).toBeVisible();
  await expect(page.getByRole("button", { name: "$10 more" })).toBeDisabled();
  page.once("dialog", (dialog) => { void dialog.accept(); });
  await page.getByRole("button", { name: "Uncapped" }).click();
  await page.getByLabel(/I authorize the paid continuation/).check();
  await page.getByRole("button", { name: "Continue uncapped" }).click();
  await expect(page.getByRole("button", { name: "Hide live status" })).toBeVisible();
  expect(posts).toEqual([{
    parent_terminal_hash: parentHash,
    plan_hash: planHash,
    combined_cap_usd: "uncapped",
    authorize_paid: true,
    confirm_uncapped: true,
    accept_compatible_update: false,
  }]);
});

test("stale plan requires refresh and clears selection and authorizations", async ({ page }) => {
  const { posts, getPreviewCount } = await openBudgetStop(page, { source: "compatible_update", postStatus: 409 });
  await page.getByRole("button", { name: "Review cost override" }).click();
  await page.getByRole("button", { name: "$10 more" }).click();
  await page.getByLabel(/I authorize the paid continuation/).check();
  await page.getByLabel(/I accept continuing with this compatible code update/).check();
  await page.getByRole("button", { name: "Add $10 and continue" }).click();
  await expect(page.getByRole("alert")).toContainText("Refresh the plan and review it again before retrying.");
  await expect(page.getByRole("button", { name: "Add $10 and continue" })).toBeDisabled();
  expect(posts).toHaveLength(1);
  await page.getByRole("button", { name: "Refresh plan" }).click();
  await expect(page.getByRole("button", { name: "$10 more" })).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByRole("checkbox", { name: /I authorize the paid continuation/ })).not.toBeChecked();
  await expect(page.getByRole("checkbox", { name: /I accept continuing with this compatible code update/ })).not.toBeChecked();
  expect(getPreviewCount()).toBe(2);
});

test("rapid duplicate clicks submit one continuation", async ({ page }) => {
  const { posts, releasePost } = await openBudgetStop(page, { delayPost: true });
  await page.getByRole("button", { name: "Review cost override" }).click();
  await page.getByRole("button", { name: "$10 more" }).click();
  await page.getByLabel(/I authorize the paid continuation/).check();
  const submit = page.getByRole("button", { name: "Add $10 and continue" });
  await submit.click();
  await expect(page.getByRole("button", { name: "Continuing…" })).toBeDisabled();
  await page.getByRole("button", { name: "Continuing…" }).click({ force: true });
  expect(posts).toHaveLength(1);
  releasePost();
  await expect(page.getByRole("button", { name: "Hide live status" })).toBeVisible();
  expect(posts).toHaveLength(1);
});

test("dashboard hides workbench continuation controls", async ({ page }) => {
  await openBudgetStop(page, { workbench: false });
  await expect(page.locator('[aria-label="Budget continuation"]')).toHaveCount(0);
  await expect(page.locator('[aria-label="Restore run"]')).toHaveCount(0);
});

test("child, batch, and non-budget stops retain ordinary restore controls", async ({ page }) => {
  await openBudgetStop(page, { parentEpisodeId: "e".repeat(32) });
  await expect(page.locator('[aria-label="Budget continuation"]')).toHaveCount(0);
  await expect(page.locator('[aria-label="Restore run"]')).toBeVisible();

  const batchPage = await page.context().newPage();
  await openBudgetStop(batchPage, { batchId: "f".repeat(32) });
  await expect(batchPage.locator('[aria-label="Budget continuation"]')).toHaveCount(0);
  await expect(batchPage.locator('[aria-label="Restore run"]')).toBeVisible();
  await batchPage.close();

  const otherStopPage = await page.context().newPage();
  await openBudgetStop(otherStopPage, { reason: "ACTION_LIMIT" });
  await expect(otherStopPage.locator('[aria-label="Budget continuation"]')).toHaveCount(0);
  await expect(otherStopPage.locator('[aria-label="Restore run"]')).toBeVisible();
  await otherStopPage.close();
});
