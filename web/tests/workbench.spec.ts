import { test, expect } from "@playwright/test";

test("synthetic run, progressive reveal, escaped annotation, verified branch", async ({
  page,
}) => {
  const failures: string[] = [];
  page.on("pageerror", (e) => failures.push(e.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: /Every choice leaves/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: /Start test episode/ }).click();
  await expect(page.getByRole("status")).toContainText("Run created");
  await expect(async () => {
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await page.getByRole("button", { name: "Review →" }).first().click();
    await expect(
      page.getByRole("heading", { name: "What was knowable here?" }),
    ).toBeVisible();
  }).toPass({ timeout: 10000 });
  await expect(
    page.getByRole("heading", { name: "Recorded decision" }),
  ).toHaveCount(0);
  await expect(
    page.getByText("SYNTHETIC TEST", { exact: false }).first(),
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
  await page.getByRole("button", { name: "Verify continuation" }).click();
  await expect(
    page.getByRole("button", { name: "Explore an alternative" }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Explore an alternative" }).click();
  await expect(
    page.getByRole("heading", { name: "Branch from this decision" }),
  ).toBeVisible();
  await page.screenshot({
    path: test.info().outputPath("workbench-review.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Skip blind", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Branch created:");
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
