import { test, expect, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";

async function exported(page: Page, format: "json" | "jsonl") {
  const download = page.waitForEvent("download");
  await page
    .getByRole("button", {
      name: `Export ${format.toUpperCase()}`,
      exact: true,
    })
    .click();
  const file = await download;
  return {
    filename: file.suggestedFilename(),
    content: await readFile((await file.path())!, "utf8"),
  };
}

async function fixture(page: Page) {
  const { operator_token } = await (
    await page.request.get("/api/bootstrap")
  ).json();
  const headers = { "X-BH-Operator": operator_token };
  const created = await page.request.post("/api/runs", {
    headers,
    data: { agent: "heuristic", offline: true },
  });
  expect(created.ok()).toBe(true);
  const { episode_id } = await created.json();
  await expect
    .poll(async () => {
      const status = await (
        await page.request.get("/api/operator/status", { headers })
      ).json();
      return status.episodes.find(
        (episode: any) => episode.episode_id === episode_id,
      )?.summary?.outcome;
    })
    .toBe("WIN");
  return episode_id;
}

test("explorer filters choices, jumps both ways, and opens retrospective annotation", async ({
  page,
}) => {
  const failures: string[] = [];
  page.on("pageerror", (e) => failures.push(e.message));
  const eid = await fixture(page);
  await page.goto("/");
  await page
    .getByRole("row")
    .filter({ hasText: eid.slice(0, 10) })
    .getByRole("button", { name: "Explore decisions" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Decision explorer", exact: true }),
  ).toBeVisible();
  const list = page.getByRole("region", { name: "Recorded choices" });
  const detail = page.getByRole("region", { name: "Decision details" });
  await expect(list.locator("button")).toHaveCount(5);
  await page
    .getByRole("combobox", { name: "Action filter", exact: true })
    .selectOption("build");
  await expect(list.locator("button")).toHaveCount(1);
  await expect(
    detail.getByRole("heading", { name: "Buy Test Joker", exact: true }),
  ).toBeVisible();
  // The visible filter narrows to one choice; downloads still contain the run.
  const writes: string[] = [];
  const observe = (request: import("@playwright/test").Request) => {
    if (request.url().includes("/api/") && request.method() !== "GET")
      writes.push(request.url());
  };
  page.on("request", observe);
  const jsonl = await exported(page, "jsonl");
  const lines = jsonl.content
    .trimEnd()
    .split("\n")
    .map((line) => JSON.parse(line));
  expect(jsonl.filename).toBe(`balatro-${eid}-decisions.jsonl`);
  expect(lines.map((line) => line.decision)).toEqual([0, 1, 2, 3, 4]);
  expect(lines.every((line) => line.snapshot_status === "finished")).toBe(true);
  const json = JSON.parse((await exported(page, "json")).content);
  expect(json.decisions).toHaveLength(5);
  expect(writes).toEqual([]);
  page.off("request", observe);
  await expect(
    detail.getByRole("button", { name: "After decision", exact: true }),
  ).toBeEnabled();
  await detail
    .getByRole("button", { name: "After decision", exact: true })
    .click();
  await expect(
    detail.getByRole("button", { name: "After decision", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Clear filters" }).click();
  await list.getByRole("button", { name: /Leave shop/ }).click();
  await expect(
    detail.getByRole("heading", { name: "Leave shop", exact: true }),
  ).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`#explore/${eid}/4$`));
  await detail
    .getByRole("button", { name: "Previous matching decision" })
    .click();
  await expect(
    detail.getByRole("heading", { name: "Buy Test Joker", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    detail.getByRole("heading", { name: "Buy Test Joker", exact: true }),
  ).toBeVisible();
  await detail.getByRole("button", { name: "Annotate this decision" }).click();
  await expect(
    page.getByText("retrospective review", { exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("Through decision")).toHaveValue("3");
  await page
    .getByLabel("What tradeoff do you see?")
    .fill("Reviewing this purchase after seeing its outcome.");
  await page
    .getByRole("button", { name: "Save assessment", exact: true })
    .click();
  await expect(page.getByText("Saved revision 1")).toBeVisible();
  await page
    .getByRole("button", { name: "Explore full run", exact: true })
    .click();
  await expect(
    detail.getByRole("heading", { name: "Buy Test Joker", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Search decisions").fill("no such choice");
  await expect(
    page.getByRole("heading", { name: "No matching decisions" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(list.locator("button")).toHaveCount(5);
  expect(failures).toEqual([]);
});

test("phone explorer switches between choices and details without horizontal overflow", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const eid = await fixture(page);
  await page.goto(`/#explore/${eid}`);
  const list = page.getByRole("region", { name: "Recorded choices" });
  const detail = page.getByRole("region", { name: "Decision details" });
  await expect(list.locator("button")).toHaveCount(5);
  await page.screenshot({
    path: test.info().outputPath("decision-explorer-mobile-list.png"),
    fullPage: true,
  });
  await list.getByRole("button", { name: /Buy Test Joker/ }).click();
  await expect(
    detail.getByRole("heading", { name: "Buy Test Joker", exact: true }),
  ).toBeVisible();
  await expect(list).toBeHidden();
  await expect(
    detail.getByRole("button", { name: "Annotate this decision" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: test.info().outputPath("decision-explorer-mobile-detail.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Back to choices" }).click();
  await expect(list).toBeVisible();
  await expect(
    list.getByRole("button", { name: /Buy Test Joker/ }),
  ).toBeFocused();
});

test("live explorer appends decisions, preserves the selected board, retries and stops at completion", async ({
  page,
}) => {
  const eid = await fixture(page);
  await page.clock.install();
  let count = 1;
  let fail = false;
  let finished = false;
  let polls = 0;
  await page.route("**/api/review/decisions", async (route) => {
    polls += 1;
    if (fail) {
      await route.fulfill({
        status: 503,
        json: { error: "TEST_CONNECTION_INTERRUPTED" },
      });
      return;
    }
    const response = await route.fetch();
    const ledger = await response.json();
    await route.fulfill({
      json: {
        ...ledger,
        actions: ledger.actions.slice(0, count),
        summary: finished ? ledger.summary : null,
        source_journal_head: `live-${count}-${finished}`,
      },
    });
  });
  await page.goto(`/#explore/${eid}`);
  const list = page.getByRole("region", { name: "Recorded choices" });
  const detail = page.getByRole("region", { name: "Decision details" });
  await expect(list.locator("button")).toHaveCount(1);
  const partial = await exported(page, "jsonl");
  expect(partial.filename).toContain("-partial.jsonl");
  const line = JSON.parse(partial.content);
  expect(line.snapshot_status).toBe("in_progress");
  expect(line.run_summary).toBe(null);
  expect(line.source_journal_head).toBe("live-1-false");
  await expect(
    page.getByText("Live · updates every 2 seconds", { exact: true }),
  ).toBeVisible();
  await expect(
    detail.getByRole("button", { name: "After decision", exact: true }),
  ).toBeEnabled();
  await detail
    .getByRole("button", { name: "After decision", exact: true })
    .click();
  const firstTitle = await detail.locator("h2").innerText();
  count = 2;
  await page.clock.runFor(2100);
  await expect(list.locator("button")).toHaveCount(2);
  await expect(detail.locator("h2")).toHaveText(firstTitle);
  await expect(
    detail.getByRole("button", { name: "After decision", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.getByLabel("Update live", { exact: true }).uncheck();
  await expect(
    page.getByText("Live updates paused", { exact: true }),
  ).toBeVisible();
  // Changing the control takes a fresh snapshot, then stops scheduling polls.
  await expect(
    page.getByRole("button", { name: "Refresh decisions" }),
  ).toBeEnabled();
  const pausedPolls = polls;
  count = 3;
  await page.clock.runFor(10000);
  expect(polls).toBe(pausedPolls);
  await expect(list.locator("button")).toHaveCount(2);
  fail = true;
  await page.getByLabel("Update live", { exact: true }).check();
  await expect(page.getByRole("alert")).toContainText(
    "TEST_CONNECTION_INTERRUPTED",
  );
  await expect(list.locator("button")).toHaveCount(2);
  fail = false;
  await page.clock.runFor(5100);
  await expect(list.locator("button")).toHaveCount(3);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.getByRole("button", { name: "Jump to latest decision" }).click();
  await expect(list.locator('button[aria-current="true"]')).toContainText("#3");
  finished = true;
  count = 5;
  await page.clock.runFor(2100);
  await expect(list.locator("button")).toHaveCount(5);
  await expect(
    page.getByText("Run finished · all recorded decisions loaded", {
      exact: true,
    }),
  ).toBeVisible();
  const finalPolls = polls;
  await page.clock.runFor(10000);
  expect(polls).toBe(finalPolls);
});
