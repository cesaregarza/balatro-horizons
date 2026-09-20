import { test, expect, type Page } from "@playwright/test";
import {
  gameMoney,
  objectNames,
  quotedPrice,
  resourceChanges,
  toolTitle,
  type Trace,
} from "../src/devTracePresentation";
import { awaitIdleWorker } from "./runHelpers";

async function setup(page: Page) {
  const { operator_token } = await (
    await page.request.get("/api/bootstrap")
  ).json();
  const headers = { "X-BH-Operator": operator_token };
  await awaitIdleWorker(page, operator_token);
  const created = await page.request.post("/api/runs", {
    headers,
    data: { agent: "heuristic", offline: true },
  });
  expect(created.ok(), await created.text()).toBe(true);
  const { episode_id } = await created.json();
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
function trace(decision: number, complete = true): Trace {
  const context = event("agent_context", {
    context: {
      current_costs: {
        cash_balance: "23",
        rerolls: { reroll_shop: { cash_cost: "5" } },
      },
      run_notebook: { entries: { plan: "Save cash" } },
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
  await expect(
    panel.getByRole("heading", { name: "Call 1 · Inspect hand levels" }),
  ).toBeVisible();
  await expect(
    panel.getByText("Tool call ID: call-67", { exact: false }),
  ).toBeVisible();
  await expect(panel.getByText("Level 2").first()).toBeVisible();
  await expect(panel.locator("pre")).toHaveCount(0);
  await panel
    .getByText("What the model was given · costs, notebook and recent memory", {
      exact: true,
    })
    .click();
  await expect(panel.getByText("Save cash", { exact: true })).toBeVisible();
  await panel
    .getByText("Technical details · raw JSON and IDs", { exact: true })
    .click();
  await panel
    .getByText("Tool arguments · inspect_page", { exact: true })
    .click();
  await expect(panel.locator("pre")).toContainText("hand_levels");
  await panel
    .getByText("Delivered context and helper exchanges", { exact: true })
    .click();
  await expect(
    panel.getByText('"cash_cost": "5"', { exact: false }),
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
    page.getByRole("heading", {
      name: "Call 1 · Write notebook: key not recorded",
    }),
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
  await expect(
    page.getByText("Tool call ID: call-68", { exact: false }),
  ).toBeVisible();
  release();
  await expect(
    page.getByRole("heading", { name: "Model tool calls · Decision 69" }),
  ).toBeVisible();
  await expect(
    page.getByText("Tool call ID: call-67", { exact: false }),
  ).toHaveCount(0);
});

test("prices, model claims, notebook mutations and observed changes remain distinct", async ({
  page,
}) => {
  const eid = await setup(page);
  const data = trace(67);
  const call = data.calls[0];
  call.tools[0] = {
    ...call.tools[0],
    name: "reroll_shop",
    arguments: {
      observation_id: 67,
      decision_note: "Use the free reroll",
      note_update: { key: "plan", text: "Keep $25" },
    },
    delivered_results: [],
  };
  call.status = "action_commit";
  call.journal_events = [
    event("run_note", {
      operation: "set_run_note",
      key: "plan",
      text: "Keep $25",
      revision: 3,
    }),
    event("action_commit", { action: { type: "reroll_shop" } }, 2),
  ];
  data.observation = { state: { resources: { money: "23" } } };
  data.transition = { state: { resources: { money: "18" } } };
  await page.route("**/api/review/decisions/*/trace", (route) =>
    route.fulfill({ json: data }),
  );
  await page.goto(`/#explore/${eid}/67`);
  await page.getByLabel("Dev mode", { exact: true }).check();
  const panel = page.getByRole("region", { name: "Model tool calls" });
  await expect(
    panel.getByText("Quoted upfront cost: $5 · Cash balance: $23"),
  ).toBeVisible();
  await expect(panel.locator(".dev-model-note").first()).toContainText(
    "Model’s noteUse the free reroll",
  );
  await expect(panel.getByText("Notebook saved: plan")).toBeVisible();
  await expect(panel.locator(".dev-settled")).toContainText("Cash: $23 → $18");
  await expect(
    panel.getByText("Game action committed", { exact: true }),
  ).toBeVisible();
  await expect(panel.locator("pre")).toHaveCount(0);
});

test("malformed and multiple calls show rejection, never inferred execution", async ({
  page,
}) => {
  const eid = await setup(page);
  const data = trace(67);
  const call = data.calls[0];
  call.tools[0] = {
    ...call.tools[0],
    name: "buy",
    arguments: '{"offer_id":',
    raw_arguments: '{"offer_id":',
    arguments_parse_error: true,
    delivered_results: [],
  };
  call.tools.push({ ...call.tools[0], call_id: "second", name: "reroll_shop" });
  call.status = "action_rejected";
  call.journal_events = [
    event("action_rejected", {
      code: "MULTIPLE_OPERATIONS",
      feedback: { message: "Return one operation" },
    }),
  ];
  await page.route("**/api/review/decisions/*/trace", (route) =>
    route.fulfill({ json: data }),
  );
  await page.goto(`/#explore/${eid}/67`);
  await page.getByLabel("Dev mode", { exact: true }).check();
  const panel = page.getByRole("region", { name: "Model tool calls" });
  await expect(
    panel.getByText("MULTIPLE_OPERATIONS", { exact: true }),
  ).toBeVisible();
  await expect(
    panel.getByText("Return one operation", { exact: true }),
  ).toBeVisible();
  await expect(panel.getByText(/Arguments are not valid JSON/)).toHaveCount(2);
  await expect(panel.locator(".dev-settled")).toContainText(
    "No settled state recorded",
  );
  await expect(
    panel.getByText("Game action committed", { exact: true }),
  ).toHaveCount(0);
});

test("presentation resolves only public identities and uses recorded quotes including zero", () => {
  const names = objectNames({
    state: {
      offers: [
        { id: "a", label: "Drunkard", effects: ["edition: POLYCHROME"] },
        { id: "b", label: "Concealed identity", face_down: true },
      ],
      hand: [{ id: "c", label: "Ace of Hearts" }],
    },
  });
  const tool = {
    ...trace(0).calls[0].tools[0],
    name: "buy",
    arguments: { offer_id: "a" },
  };
  expect(toolTitle(tool, names)).toBe("Buy Drunkard (P)");
  expect(toolTitle({ ...tool, arguments: { offer_id: "b" } }, names)).toBe(
    "Buy Face-down card",
  );
  expect(
    toolTitle(
      {
        ...tool,
        name: "play_hand",
        arguments: { card_ids: ["c", "unknown-id"] },
      },
      names,
    ),
  ).toBe("Play Ace of Hearts, unknown-id");
  expect(
    quotedPrice(
      { ...tool, name: "reroll_shop" },
      { current_costs: { rerolls: { reroll_shop: { cash_cost: "0" } } } },
    ),
  ).toBe("Quoted upfront cost: $0");
  expect(quotedPrice({ ...tool, name: "reroll_shop" }, {})).toBe(
    "Quoted upfront cost: Unknown",
  );
  expect(gameMoney(null)).toBe("Unknown");
  expect(gameMoney("")).toBe("Unknown");
  expect(gameMoney(false)).toBe("Unknown");
  expect(
    resourceChanges(
      { state: { resources: { hands: 4, money: "0" } } },
      { state: { resources: { hands: 3, money: "0.0" } } },
    ),
  ).toEqual([{ label: "Hands", before: "4", after: "3" }]);
});
