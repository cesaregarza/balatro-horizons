import { test, expect } from "@playwright/test";
import { awaitIdleWorker } from "./runHelpers";

test("synthetic run, progressive reveal, escaped annotation, replay-ready branch", async ({
  page,
}) => {
  const failures: string[] = [];
  let verificationPosts = 0;
  page.on("pageerror", (e) => failures.push(e.message));
  page.on("request", (request) => {
    if (
      request.url().endsWith("/api/verify") &&
      request.method() === "POST"
    ) {
      verificationPosts += 1;
    }
  });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Runs and experiments" }),
  ).toBeVisible();
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
  // Launch neither opens the run nor turns on the exposure-recording worker status.
  await expect(page.getByRole("region", { name: "Runs workspace" })).toBeVisible();
  await expect(page.getByText("Worker status hidden", { exact: true })).toBeVisible();
  const newRun = page.locator(".episode-card").filter({ hasText: episode_id.slice(0, 10) });
  await expect(newRun).toBeVisible();
  await expect(async () => {
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await newRun.getByRole("button", { name: "Review", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "What was knowable here?" }),
    ).toBeVisible();
  }).toPass({ timeout: 10000 });
  await expect(page.getByText("prospective review", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Research review workspace" }).getByRole("heading", { name: "Recorded decision" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("region", { name: "Research review workspace" }).getByText("SYNTHETIC TEST", { exact: false }).first(),
  ).toBeVisible();
  await page
    .getByLabel("What tradeoff do you see?")
    .fill('<img src=x onerror="window.hacked=true"> Consider future interest.');
  await page.getByRole("button", { name: "Save assessment" }).click();
  await expect(page.getByText("Saved revision 1")).toBeVisible();
  expect(
    await page.evaluate(() =>
      Object.prototype.hasOwnProperty.call(window, "hacked"),
    ),
  ).toBe(false);
  await page.getByRole("button", { name: "Reveal agent action" }).click();
  await expect(
    page.getByRole("heading", { name: "Recorded decision" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "After the action" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Reveal consequences" }).click();
  await expect(
    page.getByRole("heading", { name: "After the action" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Next decision", exact: true }).click();
  await expect(page.getByLabel("Revealed decision", { exact: true })).toHaveValue("1");
  await page.getByRole("button", { name: "Previous revealed decision" }).click();
  await expect(page.getByLabel("Revealed decision", { exact: true })).toHaveValue("0");
  await expect(page.getByRole("heading", { name: "After the action" })).toBeVisible();
  await page.getByRole("button", { name: "Return to latest revealed" }).click();
  await expect(page.getByRole("heading", { name: "Recorded decision" })).toHaveCount(0);
  await page.getByRole("button", { name: "Previous revealed decision" }).click();
  await expect(
    page.getByRole("button", { name: "Explore an alternative" }),
  ).toBeEnabled();
  await expect(
    page.getByText(/recorded snapshot is restored/),
  ).toBeVisible();
  await page.getByRole("button", { name: "Explore an alternative" }).click();
  await expect(
    page.getByRole("heading", { name: "Branch from this decision" }),
  ).toBeVisible();
  await page.screenshot({
    path: test.info().outputPath("workbench-review.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Skip blind", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Branch created:" })).toBeVisible();
  expect(verificationPosts).toBe(0);
  await page.getByRole("button", { name: "Runs", exact: true }).click();
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await page
    .getByRole("button", { name: "Compare outcomes", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("heading", {
      name: "Alternative continuation",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Original run", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Branch", exact: true }),
  ).toBeVisible();
  expect(failures).toEqual([]);
});

test("launch records no exposure for an unrelated stored run", async ({ page }) => {
  const statusPolls: string[] = [];
  await page.goto("/");
  await awaitIdleWorker(page);
  const { operator_token } = await (await page.request.get("/api/bootstrap")).json();
  const unrelated = await page.request.post("/api/runs", {
    headers: { "X-BH-Operator": operator_token },
    data: { agent: "heuristic", offline: true },
  });
  expect(unrelated.ok(), await unrelated.text()).toBe(true);
  const { episode_id: unrelatedId } = await unrelated.json();
  // From here on, only the operator status endpoint could expose the unrelated run.
  page.on("request", (request) => {
    if (request.url().endsWith("/api/operator/status")) statusPolls.push(request.url());
  });
  // Retrying Start until the worker accepts it also guarantees the unrelated run finished.
  await expect(async () => {
    const created = page.waitForResponse(
      (response) => response.url().endsWith("/api/runs") && response.request().method() === "POST",
      { timeout: 3000 },
    );
    await page.getByRole("button", { name: /Start test episode/ }).click();
    expect((await created).ok()).toBe(true);
  }).toPass({ timeout: 20000 });
  await expect(page.getByText("Worker status hidden", { exact: true })).toBeVisible();
  const unrelatedRun = page.locator(".episode-card").filter({ hasText: unrelatedId.slice(0, 10) });
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await unrelatedRun.getByRole("button", { name: "Review", exact: true }).click();
  await expect(page.getByRole("heading", { name: "What was knowable here?" })).toBeVisible();
  await expect(page.getByText("prospective review", { exact: true })).toBeVisible();
  expect(statusPolls).toEqual([]);
});

test("mobile control center remains usable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: /Start test episode/ }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: test.info().outputPath("workbench-mobile.png"),
    fullPage: true,
  });
});

test("skill access persists for new runs", async ({ page }) => {
  await page.goto("/");
  await awaitIdleWorker(page);
  await page
    .getByRole("button", { name: "Models & budgets", exact: true })
    .click();
  await expect(page.getByLabel("Skill access", { exact: true })).toHaveValue(
    "balatro-guide-v1",
  );
  await page.getByLabel("Skill access", { exact: true }).selectOption("none");
  await page
    .getByRole("button", { name: "Save skill access", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText(
    "Skill access saved for new runs.",
  );
  await page.reload();
  await page
    .getByRole("button", { name: "Models & budgets", exact: true })
    .click();
  await expect(page.getByLabel("Skill access", { exact: true })).toHaveValue(
    "none",
  );
  await page
    .getByLabel("Skill access", { exact: true })
    .selectOption("balatro-guide-v1");
  await page
    .getByRole("button", { name: "Save skill access", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText(
    "Skill access saved for new runs.",
  );
});
