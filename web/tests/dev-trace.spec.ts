import { test, expect, type Page } from "@playwright/test";

async function setup(page: Page) {
  const { operator_token } = await (
    await page.request.get("/api/bootstrap")
  ).json();
  const headers = { "X-BH-Operator": operator_token };
  const { episode_id } = await (
    await page.request.post("/api/runs", {
      headers,
      data: { agent: "heuristic", offline: true },
    })
  ).json();
  await expect
    .poll(async () => {
      const status = await (
        await page.request.get("/api/operator/status", { headers })
      ).json();
      return status.episodes.find((row: any) => row.episode_id === episode_id)
        ?.summary?.outcome;
    })
    .toBe("WIN");
  const opened = await (
    await page.request.post("/api/reviews", {
      headers,
      data: { episode_id, retrospective: true },
    })
  ).json();
  const reviewHeaders = { "X-Review-Token": opened.review_token };
  const ledger = await (
    await page.request.get("/api/review/decisions", { headers: reviewHeaders })
  ).json();
  const view = await (
    await page.request.get("/api/review/decisions/0", {
      headers: reviewHeaders,
    })
  ).json();
  const pending = [67, 68].map((decision) => ({
    event_id: `pending-${decision}`,
    decision,
    ante: 3,
    phase: "SHOP",
    type: "model_turn",
    note: null,
    status: "awaiting_model",
  }));
  await page.route("**/api/review/decisions", (route) =>
    route.fulfill({
      json: {
        ...ledger,
        actions: [],
        uncommitted_actions: [],
        pending_decisions: pending,
        summary: null,
      },
    }),
  );
  await page.route(/\/api\/review\/decisions\/(67|68)$/, (route) =>
    route.fulfill({
      json: {
        ...view,
        decision: Number(route.request().url().split("/").at(-1)),
        transition: null,
      },
    }),
  );
  return episode_id;
}

const event = (type: string, payload: any, sequence = 1) => ({
  event_id: `${type}-${sequence}`,
  sequence,
  timestamp: "2026-09-17T10:00:00Z",
  type,
  payload,
});
function trace(decision: number, complete = true) {
  const context = event("agent_context", {
    context: {
      current_costs: { reroll_shop: "5" },
      run_notebook: { plan: "Save cash" },
    },
  });
  return {
    decision,
    complete,
    observation: {},
    transition: null,
    events: [context],
    omissions: ["Opaque provider continuations are omitted."],
    linkage: "Results use provider call IDs.",
    calls: [
      {
        request_id: `request-${decision}`,
        context_event_id: context.event_id,
        request_event: event("provider_request", {
          attempt: 1,
          reserved_usd: 0.02,
          body: { input: "Exact delivered request" },
        }),
        response_event: event("provider_response", {
          cost_usd: 0.001,
          body: { usage: { input_tokens: 2000 }, output: [] },
        }),
        error_event: null,
        status: "helper_result",
        tools: [
          {
            call_id: `call-${decision}`,
            name: "inspect_page",
            arguments: {
              page: "hand_levels",
              nested: {
                text:
                  '<img src=x onerror="window.injected=true">' +
                  "x".repeat(1000),
              },
            },
            raw_arguments: "{}",
            arguments_parse_error: false,
            delivered_results: [
              {
                request_id: "next-request",
                content: { Pair: "Level 2" },
                content_parse_error: false,
              },
            ],
          },
        ],
        journal_events: [
          event("helper_result", { result: { Pair: "Level 2" } }, 2),
        ],
      },
    ],
  };
}

test("dev mode lazily reveals full calls on helper-only decisions, including mobile hostile text", async ({
  page,
}) => {
  const eid = await setup(page);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  let requests = 0;
  await page.route("**/api/review/decisions/*/trace", (route) => {
    requests += 1;
    return route.fulfill({ json: trace(67) });
  });
  await page.goto(`/#explore/${eid}/67`);
  await expect(
    page.getByRole("heading", { name: "No game actions recorded yet" }),
  ).toBeVisible();
  expect(requests).toBe(0);
  await page.getByLabel("Dev mode", { exact: true }).check();
  const panel = page.getByRole("region", { name: "Model tool calls" });
  await expect(
    panel.getByRole("heading", { name: "Model tool calls · Decision 68" }),
  ).toBeVisible();
  await expect(panel.getByText("Tool call ID: call-67")).toBeVisible();
  await panel.getByText("Tool arguments", { exact: true }).click();
  await expect(panel.locator("pre")).toContainText("hand_levels");
  await panel
    .getByText("Result sent back to model (matched call ID)", { exact: true })
    .click();
  await expect(
    panel.getByText('"Pair": "Level 2"', { exact: false }).first(),
  ).toBeVisible();
  await panel
    .getByText("Delivered context and helper exchanges", { exact: true })
    .click();
  await expect(
    panel.getByText('"reroll_shop": "5"', { exact: false }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .getByRole("region", { name: "Recorded choices" })
    .getByRole("button", { name: /#68/ })
    .click();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(await page.evaluate(() => (window as any).injected)).toBeUndefined();
  expect(errors).toEqual([]);
  await page.getByLabel("Dev mode", { exact: true }).uncheck();
  await expect(panel).toHaveCount(0);
});

test("only incomplete traces poll; disabling dev mode stops its requests", async ({
  page,
}) => {
  const eid = await setup(page);
  let count = 0;
  await page.route("**/api/review/decisions/*/trace", (route) => {
    count += 1;
    const data = trace(67, false);
    if (count > 1) data.calls[0].tools[0].name = "set_run_note";
    return route.fulfill({ json: data });
  });
  await page.goto(`/#explore/${eid}/67`);
  await page.getByLabel("Dev mode", { exact: true }).check();
  await expect(
    page.getByRole("heading", { name: "Call 1 · set_run_note" }),
  ).toBeVisible();
  await page.getByLabel("Dev mode", { exact: true }).uncheck();
  const atDisable = count;
  await page.waitForTimeout(2300);
  expect(count).toBe(atDisable);
});

test("changing selection cannot display a late response from the previous decision", async ({
  page,
}) => {
  const eid = await setup(page);
  let started = false;
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/review/decisions/67/trace", async (route) => {
    started = true;
    await held;
    await route.fulfill({ json: trace(67) }).catch(() => {});
  });
  await page.route("**/api/review/decisions/68/trace", (route) =>
    route.fulfill({ json: trace(68) }),
  );
  await page.goto(`/#explore/${eid}/67`);
  await page.getByLabel("Dev mode", { exact: true }).check();
  await expect.poll(() => started).toBe(true);
  await page
    .getByRole("region", { name: "Recorded choices" })
    .getByRole("button", { name: /#69/ })
    .click();
  await expect(page.getByText("Tool call ID: call-68")).toBeVisible();
  release();
  await expect(
    page.getByRole("heading", { name: "Model tool calls · Decision 69" }),
  ).toBeVisible();
  await expect(page.getByText("Tool call ID: call-67")).toHaveCount(0);
});
