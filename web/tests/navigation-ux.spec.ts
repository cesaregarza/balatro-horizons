import { test, expect } from "@playwright/test";
import { explorerEpisode, mockExplorer } from "./explorerFixture";

test("empty destinations explain how to choose a run and participate in browser history", async ({ page }) => {
  await mockExplorer(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Decision explorer", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Choose a run to explore" })).toBeVisible();
  await expect(page).toHaveURL(/#explore$/);
  await page.getByRole("button", { name: "Open run library" }).click();
  await expect(page.getByRole("heading", { name: "Runs and experiments" })).toBeVisible();
  await page.goBack();
  await expect(page.getByRole("heading", { name: "Choose a run to explore" })).toBeVisible();
  await page.getByRole("button", { name: "Horizon review", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Choose a run for staged review" })).toBeVisible();
});

test("replacing an inspected run through a URL cannot discard an assessment silently", async ({ page }) => {
  await mockExplorer(page);
  await page.goto(`/#explore/${explorerEpisode}/20`);
  await page.getByRole("button", { name: "Annotate this decision" }).click();
  await page.getByLabel("What tradeoff do you see?").fill("Keep this reasoning draft.");
  page.once("dialog", (dialog) => { void dialog.dismiss(); });
  await page.evaluate(() => { window.location.hash = `#explore/${"f".repeat(32)}/0`; });
  await expect(page).toHaveURL(new RegExp(`#explore/${explorerEpisode}/20$`));
  await expect(page.getByLabel("What tradeoff do you see?")).toHaveValue("Keep this reasoning draft.");
  await page.getByRole("button", { name: "Models & budgets", exact: true }).click();
  await page.getByRole("button", { name: "Decision explorer", exact: true }).click();
  await expect(page.getByLabel("What tradeoff do you see?")).toHaveValue("Keep this reasoning draft.");
});
