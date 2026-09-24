import { test, expect, type Page } from "@playwright/test";
import type { RestorePreview } from "../src/api/client";

const eid = "a".repeat(32);
const parent = "c".repeat(64);
const planHash = "d".repeat(64);

function available(compatibility: "same_source" | "compatible_update" = "same_source"): RestorePreview {
  return {
    episode_id: eid, available: true, reason: null,
    plan: {
      parent_head: parent, plan_hash: planHash, decision: 6,
      requires_paid_authorization: true, source_compatibility: compatibility,
      costs: { accounted_usd: 0.42, remaining_episode_usd: 0.58, remaining_batch_usd: 1.58 },
      limits: { max_episode_cost_usd: 1, max_batch_cost_usd: 2 },
      launches: { verification: 0, continuation: 1 },
    },
  };
}

async function app(page: Page, preview: RestorePreview, options: { workbench?: boolean; delayPost?: boolean } = {}) {
  const posts: { url: string; body: unknown }[] = [];
  let releasePost = () => {};
  const postGate = new Promise<void>((resolve) => { releasePost = resolve; });
  const workbench = options.workbench ?? true;
  await page.route("**/api/bootstrap", (route) => route.fulfill({ json: {
    operator_token: "mock-operator", config: { workbench, models: {}, model_capabilities: {}, budgets: {} }, workbench, paid_credentials: {},
  } }));
  await page.route("**/api/episodes", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/panels", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/batches", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/operator/status", (route) => route.fulfill({ json: {
    running: false, active_episode: null, episodes: [], error: null,
  } }));
  await page.route("**/api/explore/sessions", (route) => route.fulfill({ json: { review_token: "mock-review", view: null } }));
  await page.route("**/api/explore/decisions", (route) => route.fulfill({ json: {
    source_journal_head: parent, spend: { accounted_usd: 0.42, response_usd: 0.42, reserved_usd: 0 },
    summary: { outcome: "INFRASTRUCTURE_FAILURE" },
    manifest: { episode_id: eid, agent: "fixture", evidence_kind: "NATIVE", evaluation_eligible: false, config: { models: {}, model_capabilities: {} } },
    actions: [], uncommitted_actions: [], rounds: [],
  } }));
  await page.route("**/api/operator/episodes/*/restore", async (route) => {
    if (route.request().method() === "POST") {
      posts.push({ url: route.request().url(), body: route.request().postDataJSON() });
      if (options.delayPost) await postGate;
      return route.fulfill({ json: { episode_id: "b".repeat(32) } });
    }
    return route.fulfill({ json: preview });
  });
  await page.goto(`/#explore/${eid}`);
  await expect(page.getByRole("heading", { name: "Decision explorer" })).toBeVisible();
  return { posts, releasePost };
}

test("restore preview is read-only until explicit confirmation", async ({ page }) => {
  const { posts } = await app(page, available());
  const spend = page.getByRole("region", { name: "Run API spend" });
  await expect(spend.locator(".run-spend-total")).toHaveText("$0.42");
  await page.getByRole("button", { name: "Restore run" }).click();
  await expect(page.getByText("Latest checkpoint: resume before decision 7.")).toBeVisible();
  await expect(page.getByText("Spend so far: $0.42. Episode cap: $1.00 total; $0.58 remaining. Batch cap: $2.00 total; $1.58 remaining.")).toBeVisible();
  await expect(page.getByText("Launches: 0 preliminary checks and 1 checked restore, in the same game instance.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Restore and continue" })).toBeDisabled();
  expect(posts).toEqual([]);
  await page.getByLabel(/I authorize the paid continuation/).check();
  await page.getByRole("button", { name: "Restore and continue" }).click();
  await expect(page.getByRole("button", { name: "Hide live status" })).toBeVisible();
  await expect(page).not.toHaveURL(/#explore/);
  expect(posts[0]?.url).toMatch(new RegExp(`/api/operator/episodes/${eid}/restore$`));
  expect(posts[0]?.body).toEqual({
    parent_head: parent, plan_hash: planHash, authorize_paid: true, accept_compatible_update: false,
  });
});

test("blocked plans explain the reason without a submit action", async ({ page }) => {
  const { posts } = await app(page, { episode_id: eid, available: false, reason: "WORKER_BUSY", plan: null });
  await page.getByRole("button", { name: "Restore run" }).click();
  await expect(page.getByText("Restore unavailable: WORKER_BUSY")).toBeVisible();
  await expect(page.getByRole("button", { name: "Restore and continue" })).toHaveCount(0);
  expect(posts).toEqual([]);
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByRole("button", { name: "Restore run" })).toBeVisible();
});

test("compatible update is disclosed and requires explicit acceptance", async ({ page }) => {
  const { posts } = await app(page, available("compatible_update"));
  await page.getByRole("button", { name: "Restore run" }).click();
  await expect(page.getByText(/Compatible code update:/)).toBeVisible();
  await expect(page.getByText("Historical protocol identity is preserved.")).toBeVisible();
  await page.getByLabel(/I authorize the paid continuation/).check();
  const submit = page.getByRole("button", { name: "Restore and continue" });
  await expect(submit).toBeDisabled();
  await page.getByLabel(/I accept continuing with this compatible code update/).check();
  await submit.click();
  await expect(page.getByRole("button", { name: "Hide live status" })).toBeVisible();
  expect(posts[0]?.body).toEqual({ parent_head: parent, plan_hash: planHash, authorize_paid: true, accept_compatible_update: true });
});

test("submit failures keep the confirmation open and require a fresh plan", async ({ page }) => {
  const posts: unknown[] = [];
  let previews = 0;
  await page.route("**/api/bootstrap", (route) => route.fulfill({ json: { operator_token: "mock", config: { workbench: true, models: {}, model_capabilities: {}, budgets: {} }, workbench: true, paid_credentials: {} } }));
  await page.route("**/api/episodes", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/panels", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/batches", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/operator/status", (route) => route.fulfill({ json: { running: false, active_episode: null, episodes: [], error: null } }));
  await page.route("**/api/explore/sessions", (route) => route.fulfill({ json: { review_token: "mock-review", view: null } }));
  await page.route("**/api/explore/decisions", (route) => route.fulfill({ json: { summary: { outcome: "INFRASTRUCTURE_FAILURE" }, manifest: { episode_id: eid, agent: "fixture", evidence_kind: "NATIVE", evaluation_eligible: false, config: { models: {}, model_capabilities: {} } }, actions: [], uncommitted_actions: [], rounds: [] } }));
  await page.route("**/api/operator/episodes/*/restore", async (route) => {
    if (route.request().method() === "POST") { posts.push(route.request().postDataJSON()); return route.fulfill({ status: 409, json: { error: "RESTORE_PLAN_CHANGED" } }); }
    previews += 1;
    return route.fulfill({ json: available() });
  });
  await page.goto(`/#explore/${eid}`);
  await page.getByRole("button", { name: "Restore run" }).click();
  await page.getByLabel(/I authorize the paid continuation/).check();
  const submit = page.getByRole("button", { name: "Restore and continue" });
  await submit.click();
  await expect(page.getByRole("alert")).toContainText("Refresh the plan and review it again before retrying.");
  await expect(submit).toBeDisabled();
  expect(posts).toHaveLength(1);
  await page.getByRole("button", { name: "Refresh plan" }).click();
  await expect(page.getByRole("checkbox", { name: /I authorize the paid continuation/ })).not.toBeChecked();
  expect(previews).toBe(2);
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByRole("button", { name: "Restore run" })).toBeVisible();
});

test("read-only dashboard hides the restore control", async ({ page }) => {
  await app(page, available(), { workbench: false });
  await expect(page.getByRole("region", { name: "Restore run" })).toHaveCount(0);
});

test("rapid duplicate clicks send only one restore request", async ({ page }) => {
  const { posts, releasePost } = await app(page, available(), { delayPost: true });
  await page.getByRole("button", { name: "Restore run" }).click();
  await page.getByLabel(/I authorize the paid continuation/).check();
  const submit = page.getByRole("button", { name: "Restore and continue" });
  await submit.click();
  const pending = page.getByRole("button", { name: "Restoring…" });
  await expect(pending).toBeDisabled();
  await pending.click({ force: true });
  expect(posts).toHaveLength(1);
  releasePost();
  await expect(page.getByRole("button", { name: "Hide live status" })).toBeVisible();
  expect(posts).toHaveLength(1);
});
