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

  test(`${provider} inspector merges status deltas and replaces edited/deleted notes`, () => {
    const initial = { observation: { phase: "SHOP", remaining_budget: { provider_calls: 10, dollars: 5 } },
      run_notebook: { entries: { plan: "initial", obsolete: "delete me" } },
      helper_status: { remaining: 4, message: "shared allowance" },
      notebook_maintenance: { message: "save conclusions", oldest_decision_leaves_after_action: null } };
    const updates = [
      { run_notebook: { entries: { plan: "changed" } }, helper_status: { remaining: 3 },
        remaining_budget: { provider_calls: 9 } },
      { helper_status: { remaining: 2 }, remaining_budget: { provider_calls: 8 },
        notebook_maintenance: { oldest_decision_leaves_after_action: { decision_id: 1 } } },
      { run_notebook: { entries: {} }, remaining_budget: { provider_calls: 7 } },
    ];
    const messages = [{ role: "user", content: JSON.stringify(initial) }];
    const body = { [provider === "openai" ? "input" : "messages"]: messages };
    for (const [index, update] of updates.entries()) {
      const output = JSON.stringify({ result: { ok: true }, context_update: update });
      messages.push(provider === "openai" ? { type: "function_call_output", output }
        : { role: "user", content: [{ type: "tool_result", content: output }] });
      const before = JSON.stringify(body);
      const shown = recordedModelContext(body);
      assert.deepEqual(shown.run_notebook.entries, index === 2 ? {} : { plan: "changed" });
      assert.equal(shown.helper_status.remaining, index === 0 ? 3 : 2);
      assert.equal(shown.helper_status.message, "shared allowance");
      assert.equal(shown.notebook_maintenance.message, "save conclusions");
      assert.deepEqual(shown.notebook_maintenance.oldest_decision_leaves_after_action,
        index === 0 ? null : { decision_id: 1 });
      assert.deepEqual(shown.observation.remaining_budget, { provider_calls: 9 - index, dollars: 5 });
      assert.equal(JSON.stringify(body), before);
    }
  });
}
