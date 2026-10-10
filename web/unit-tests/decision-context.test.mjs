import assert from "node:assert/strict";
import test from "node:test";
import { recordedModelContext } from "../.unit-dist/modelContext.js";

for (const provider of ["openai", "anthropic"]) {
  test(`${provider} inspector folds appended context updates without changing recorded input`, () => {
    const initial = { observation: { phase: "SHOP", remaining_budget: { provider_calls: 10 } },
      run_notebook: { entries: { plan: "initial" } }, current_costs: { cash_balance: 5 } };
    const content = JSON.stringify(initial);
    const update = { run_notebook: { entries: { plan: "new note" } },
      remaining_budget: { provider_calls: 7, as_of_provider_attempt: 3 },
      permitted_tools: ["abort_run"], helper_status: { remaining: 0 } };
    const output = JSON.stringify({ result: { ok: true }, context_update: update });
    const messages = [{ role: "user", content }, ...(provider === "openai"
      ? [{ type: "function_call_output", call_id: "call", output }]
      : [{ role: "user", content: [{ type: "tool_result", tool_use_id: "call", content: output }] }])];
    const body = { [provider === "openai" ? "input" : "messages"]: messages };
    const before = JSON.stringify(body);
    const shown = recordedModelContext(body);
    assert.deepEqual(shown.run_notebook, update.run_notebook);
    assert.deepEqual(shown.observation.remaining_budget, update.remaining_budget);
    assert.deepEqual(shown.permitted_tools, ["abort_run"]);
    assert.deepEqual(shown.current_costs, initial.current_costs);
    assert.equal(shown.observation.phase, "SHOP");
    assert.equal(JSON.stringify(body), before);
    assert.deepEqual(recordedModelContext({ input: [{ role: "user", content }] }), initial);
  });
}
