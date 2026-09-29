import { test, expect } from "@playwright/test";
import { explorerEpisode, mockExplorer } from "./explorerFixture";

test("annotation numbers are one-based, bounds are enforced, and revisions retain stored IDs", async ({ page }) => {
  const fixture = await mockExplorer(page);
  await page.goto(`/#explore/${explorerEpisode}/20`);
  await page.getByRole("button", { name: "Annotate this decision" }).click();
  await expect(page.getByLabel("From decision")).toHaveValue("21");
  await expect(page.getByLabel("Through decision")).toHaveValue("21");
  await expect(page.getByText("Selected decision #21", { exact: true })).toBeVisible();
  await page.getByLabel("What tradeoff do you see?").fill("Assessing the revealed interval.");
  await page.getByLabel("From decision").fill("0");
  await expect(page.getByRole("button", { name: "Save assessment", exact: true })).toBeDisabled();
  await page.getByLabel("From decision").fill("22");
  await expect(page.getByRole("button", { name: "Save assessment", exact: true })).toBeDisabled();
  await page.getByLabel("From decision").fill("19");
  await page.getByRole("button", { name: "Save assessment", exact: true }).click();
  await expect(page.getByText("Saved revision 1")).toBeVisible();
  expect(fixture.writes[0].body).toMatchObject({ start_decision: 18, end_decision: 20, annotation_id: null });
  await page.getByText("Annotations and revisions (1)", { exact: true }).click();
  await page.getByRole("button", { name: "Revise", exact: true }).click();
  await expect(page.getByLabel("From decision")).toHaveValue("19");
  await expect(page.getByLabel("Through decision")).toHaveValue("21");
  await page.getByLabel("What tradeoff do you see?").fill("A revised assessment.");
  await page.getByRole("button", { name: "Save revision", exact: true }).click();
  await expect(page.getByText("Saved revision 2")).toBeVisible();
  expect(fixture.writes[1].body).toMatchObject({ start_decision: 18, end_decision: 20, annotation_id: "annotation-one" });
});

test("an unsaved annotation survives tabs and blocks accidental close or decision switching", async ({ page }) => {
  await mockExplorer(page);
  await page.goto(`/#explore/${explorerEpisode}/20`);
  await page.getByRole("button", { name: "Annotate this decision" }).click();
  const note = page.getByLabel("What tradeoff do you see?");
  await expect(note).toBeFocused();
  await note.fill("Keep this draft attached to decision 21.");
  await page.getByRole("navigation").getByRole("button", { name: "Models & budgets" }).click();
  await page.getByRole("navigation").getByRole("button", { name: "Decision explorer", exact: true }).click();
  await expect(note).toHaveValue("Keep this draft attached to decision 21.");
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("region", { name: "Decision details" }).getByRole("button", { name: "Next matching decision" }).click();
  await expect(page.getByRole("region", { name: "Decision details" }).locator(".eyebrow")).toContainText("DECISION 21");
  await expect(note).toHaveValue("Keep this draft attached to decision 21.");
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("button", { name: "Explore full run", exact: true }).click();
  await expect(note).toBeVisible();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Explore full run", exact: true }).click();
  await expect(note).toHaveCount(0);
});

test("an expired session keeps the annotation draft and explains how to recover", async ({ page }) => {
  await mockExplorer(page);
  await page.route("**/api/explore/annotations", (route) => route.request().method() === "POST"
    ? route.fulfill({ status: 403, json: { error: "REVIEW_TOKEN_EXPIRED" } })
    : route.fulfill({ json: [] }));
  await page.goto(`/#explore/${explorerEpisode}/20`);
  await page.getByRole("button", { name: "Annotate this decision" }).click();
  await page.getByLabel("What tradeoff do you see?").fill("Retain my assessment.");
  await page.getByRole("button", { name: "Save assessment", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("This inspection session has expired");
  await expect(page.getByLabel("What tradeoff do you see?")).toHaveValue("Retain my assessment.");
  await expect(page.getByText("Selected decision #21 · Unsaved draft", { exact: true })).toBeVisible();
});

test("browser history can inspect another decision without retargeting an unsaved annotation", async ({ page }) => {
  await mockExplorer(page);
  await page.goto(`/#explore/${explorerEpisode}/19`);
  const detail = page.getByRole("region", { name: "Decision details" });
  await expect(detail.locator(".eyebrow")).toContainText("DECISION 20");
  await detail.getByRole("button", { name: "Next matching decision" }).click();
  await expect(detail.locator(".eyebrow")).toContainText("DECISION 21");
  await detail.getByRole("button", { name: "Annotate this decision" }).click();
  await page.getByLabel("What tradeoff do you see?").fill("This draft belongs to decision 21.");
  await page.goBack();
  await expect(detail.locator(".eyebrow")).toContainText("DECISION 20");
  await expect(page.getByLabel("Through decision")).toHaveValue("21");
  await expect(page.getByText("Selected decision #21 · Unsaved draft", { exact: true })).toBeVisible();
  await expect(page.getByLabel("What tradeoff do you see?")).toHaveValue("This draft belongs to decision 21.");
  await page.goForward();
  await expect(detail.locator(".eyebrow")).toContainText("DECISION 21");
  await expect(page.getByLabel("What tradeoff do you see?")).toHaveValue("This draft belongs to decision 21.");
});
