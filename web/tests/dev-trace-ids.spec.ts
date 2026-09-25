import { test, expect, type Page } from "@playwright/test";
import {
  gameMoney, objectNames, quotedPrice, recordedModelContext, toolTitle, type Trace, type TraceCall, type TraceEvent,
} from "../src/devTracePresentation";
import { awaitIdleWorker } from "./runHelpers";

const event = (type: string, payload: any): TraceEvent => ({
  event_id: type, sequence: 1, timestamp: "fixture", type, payload,
});

async function syntheticRun(page: Page) {
  const { operator_token } = await (await page.request.get("/api/bootstrap")).json();
  await awaitIdleWorker(page, operator_token);
  const created = await page.request.post("/api/runs", {
    headers: { "X-BH-Operator": operator_token }, data: { agent: "heuristic", offline: true },
  });
  expect(created.ok(), await created.text()).toBe(true);
  const { episode_id } = await created.json();
  await awaitIdleWorker(page, operator_token);
  return episode_id;
}

for (const provider of ["openai", "anthropic"]) {
  test(`${provider} compact IDs keep inspector names and quoted prices readable`, async ({ page }) => {
    const eid = await syntheticRun(page);
    const delivered = {
      observation: {
        state: { offers: [{ id: 7, label: "Eternal Joker", effects: ["eternal: True"],
          quote: { cash_cost: "2", operation: "buy", affordable: true,
            purchase_modes: { acquire: { status: "legal" } },
            target_count: { min: 0, max: 0 }, recorded_obligations: [] } }] },
        last_action: { result: "receipt-7" },
      },
      current_costs: { cash_balance: "20" },
      working_memory: { frames: [{ observed_result_ref: "/observation/last_action" }] },
    };
    const contextEvent = event("agent_context", { context: {
      observation: { state: { offers: [{ id: "backend-id", label: "Backend object" }] } },
      current_costs: { cash_balance: "20" },
    } });
    const call: TraceCall = {
      request_id: "request", context_event_id: contextEvent.event_id,
      request_event: event("provider_request", { body: {
        [provider === "openai" ? "input" : "messages"]: [
          { role: "user", content: JSON.stringify(delivered) },
        ],
      } }),
      response_event: null, error_event: null, status: "response_received",
      journal_events: [], tools: [{
        call_id: "tool", name: "buy", arguments: { offer_id: 7, quantity: 7 },
        raw_arguments: null, arguments_parse_error: false, delivered_results: [],
      }],
    };
    const data: Trace = {
      decision: 0, complete: true, calls: [call],
      events: provider === "openai" ? [contextEvent] : [],
      observation: contextEvent.payload.context.observation, transition: null,
      omissions: [], linkage: "fixture",
    };
    await page.route("**/api/explore/decisions/*/trace", (route) => route.fulfill({ json: data }));
    await page.goto(`/#explore/${eid}/0`);
    await page.getByLabel("Dev mode", { exact: true }).check();
    const panel = page.getByRole("region", { name: "Model tool calls" });
    await expect(panel.getByText("Requested: Buy Eternal Joker (∞)", { exact: true })).toBeVisible();
    await expect(panel.getByText("Quoted upfront cost: $2 · Cash balance: $20")).toBeVisible();
    await expect(panel.getByText("item not recorded", { exact: false })).toHaveCount(0);
    // Only ID-aware titles resolve numbers; quantities and scores stay numeric.
    await expect(panel.locator(".dev-tool dd").filter({ hasText: /^7$/ })).toHaveCount(2);
    await panel.getByText("What the model was given · costs, offers, notebook and recent memory").click();
    await expect(panel.getByText("No context event recorded for this request.")).toHaveCount(0);
    await expect(panel.getByText("Offers with delivered quotes", { exact: true })).toBeVisible();
    await expect(panel.getByText("Effects", { exact: true })).toBeVisible();
    await expect(panel.getByText("eternal: True", { exact: true })).toBeVisible();
    await expect(panel.getByText("Latest action receipt · observation.last_action", { exact: true })).toBeVisible();
    await expect(panel.getByText("receipt-7", { exact: true })).toBeVisible();
    const memoryFrames = panel.getByText("Frames", { exact: true }).locator("..");
    await memoryFrames.getByRole("button", { name: "Show 1 fields", exact: true }).click();
    await expect(panel.getByText("/observation/last_action", { exact: true })).toBeVisible();
  });
}

test("legacy fallback and concealed or unknown integer identities remain honest", () => {
  expect(recordedModelContext({ input: "old diagnostic fixture" })).toBeNull();
  expect(recordedModelContext({ input: [
    { role: "user", content: "malformed" },
    { role: "user", content: '{"observation":{"state":{}}}' },
  ] })).toBeNull();
  const names = objectNames({ state: { offers: [
    { id: "legacy", label: "Legacy Joker" },
    { id: 7, label: "Secret identity", face_down: true },
  ] } });
  const tool = { call_id: "tool", name: "buy", raw_arguments: null,
    arguments_parse_error: false, delivered_results: [] };
  expect(toolTitle({ ...tool, arguments: { offer_id: "legacy" } }, names)).toBe("Buy Legacy Joker");
  expect(toolTitle({ ...tool, arguments: { offer_id: 7 } }, names)).toBe("Buy Face-down card");
  expect(toolTitle({ ...tool, arguments: { offer_id: 99 } }, names)).toBe("Buy 99");
  expect(toolTitle({ ...tool, arguments: { offer_id: true } }, names)).toBe("Buy item not recorded");
});

test("inline quotes preserve zero and explicit unknown over legacy prices", () => {
  const tool = { call_id: "tool", name: "buy", raw_arguments: null,
    arguments_parse_error: false, delivered_results: [], arguments: { offer_id: 7 } };
  expect(quotedPrice(tool, {
    observation: { state: { offers: [{ id: 7, quote: { cash_cost: 0 } }] } },
    current_costs: { offers: [{ offer_id: 7, cash_cost: 5 }] },
  })).toBe("Quoted upfront cost: $0");
  expect(quotedPrice(tool, {
    observation: { state: { offers: [{ id: 7, quote: null }] } },
    current_costs: { offers: [{ offer_id: 7, cash_cost: 5 }] },
  })).toBe("Quoted upfront cost: Unknown");
  expect(quotedPrice(tool, {
    observation: { state: { offers: [{ id: 7, quote: { cash_cost: null } }] } },
    current_costs: { offers: [{ offer_id: 7, cash_cost: 5 }] },
  })).toBe("Quoted upfront cost: Unknown");
  expect(quotedPrice(tool, {
    observation: { state: { offers: [{ id: 7, quote: "malformed" }] } },
    current_costs: { offers: [{ offer_id: 7, cash_cost: 5 }] },
  })).toBe("Quoted upfront cost: Unknown");
  expect(quotedPrice(tool, { current_costs: { offers: [{ offer_id: 7, cash_cost: 5 }] } }))
    .toBe("Quoted upfront cost: $5");
  expect(quotedPrice({ ...tool, arguments: { offer_id: 8 } }, {
    observation: { state: { offers: [{ id: 7, quote: { cash_cost: 0 } }] } },
    current_costs: { offers: [{ offer_id: 7, cash_cost: 5 }] },
  })).toBe("Quoted upfront cost: Unknown");
  expect(gameMoney(7)).toBe("$7");
});
