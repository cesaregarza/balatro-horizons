import type { Page } from "@playwright/test";
import type { DecisionLedger, Observation, RestorePreview, View } from "../src/api/client";

export const explorerEpisode = "e".repeat(32);
export async function mockExplorer(page: Page, options: { live?: boolean; outcome?: string; restore?: RestorePreview; providerReporting?: boolean } = {}) {
  const writes: { path: string; body: any }[] = [];
  let polls = 0;
  let previews = 0;
  let annotations: any[] = [];
  const ledger: DecisionLedger = {
    source_journal_head: "fixture-head",
    manifest: { episode_id: explorerEpisode, agent: "heuristic", evidence_kind: "SYNTHETIC_TEST", evaluation_eligible: false, config: { deck: "Red", stake: "White", models: {} } },
    summary: options.live ? null : { outcome: options.outcome || "INFRASTRUCTURE_FAILURE", reason: options.outcome === "WIN" ? undefined : "PROVIDER_RESPONSE_INVALID" },
    spend: { accounted_usd: 0.42, response_usd: 0.42, reserved_usd: 0 },
    actions: Array.from({ length: 30 }, (_, decision) => ({ event_id: `action-${decision}`, decision, action_number: decision + 1, ante: 1, phase: "SHOP", type: "leave_shop", note: `Recorded note ${decision + 1}` })),
    uncommitted_actions: [], rounds: [],
    pending_decisions: options.outcome === "WIN" ? [] : [{ event_id: "attempt-30", decision: 30, ante: 1, phase: "SHOP", type: "model_turn", status: "no_game_action", note: null }],
  };
  const observation = (decision: number, after = false): Observation => ({
    episode_id: explorerEpisode, observation_id: decision, phase: "SHOP", memory: "",
    available_action_types: [], action_constraints: {}, remaining_budget: {},
    state: { progress: { ante: 1, blind: "Small" }, resources: { money: decision + (after ? 1 : 0), hands: 4, discards: 3, chips: 0, target: 300 }, hand: [], jokers: [], consumables: [], offers: [], revealed_blinds: [], hand_levels: {}, persistent_effects: [] },
  });
  if (options.providerReporting) Object.assign(ledger.manifest, {
    recorded_interface: "tools_v8", context_policy: "append_only_decision_v1",
    provider_wire_policy: "anthropic_messages_v1",
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/bootstrap") return route.fulfill({ json: { operator_token: "mock-operator", config: { workbench: true, models: {}, model_capabilities: { models: {} }, budgets: {} }, workbench: true, paid_credentials: {} } });
    if (["/api/episodes", "/api/operator/episodes", "/api/panels", "/api/batches"].includes(path)) return route.fulfill({ json: [] });
    if (path === "/api/operator/runtime") return route.fulfill({ json: { ready: false, code: "RUNTIME_NOT_RUNNING", message: "No game runtime is attached to this browser fixture." } });
    if (path === "/api/operator/status") return route.fulfill({ json: { running: false, active_episode: null, episodes: [] } });
    if (path === "/api/explore/sessions") return route.fulfill({ json: { review_token: "mock-explorer", view: null } });
    if (path === "/api/explore/decisions") { polls += 1; return route.fulfill({ json: ledger }); }
    if (/\/api\/explore\/decisions\/\d+$/.test(path)) {
      const decision = Number(path.split("/").at(-1));
      const view: View = { episode_id: explorerEpisode, decision, stage: "transition", observation: observation(decision), transition: decision < 30 ? observation(decision, true) : undefined, evidence_kind: "SYNTHETIC_TEST", evaluation_eligible: false, fixture: null, action_events: [], review_mode: "retrospective", exposure: {}, trajectory: [] };
      if (options.providerReporting) view.action_events = [
        { texts: ["Claude returned public summary."], status: "returned", redacted: true },
        { texts: ["OpenAI returned public summary."], status: "returned", redacted: false },
        { texts: [], status: "empty", redacted: false },
        { texts: [], status: "redacted", redacted: true },
        { texts: [], status: "absent", redacted: false },
        { texts: [], status: "unsupported", redacted: false },
      ].map((returned_reasoning, index) => ({ type: "provider_response", event_id: `response-${index}`, payload: { returned_reasoning } }));
      return route.fulfill({ json: view });
    }
    if (path === "/api/explore/annotations") {
      if (request.method() === "GET") return route.fulfill({ json: annotations });
      const body = request.postDataJSON();
      writes.push({ path, body });
      const annotation = { ...body, annotation_id: body.annotation_id || "annotation-one", revision: annotations.length + 1, review_mode: "retrospective" };
      annotations = [...annotations, annotation];
      return route.fulfill({ json: annotation });
    }
    if (path.endsWith("/restore")) {
      if (request.method() !== "GET") { writes.push({ path, body: request.postDataJSON() }); return route.fulfill({ status: 409, json: { error: "UNEXPECTED_RESTORE_WRITE" } }); }
      previews += 1;
      return route.fulfill({ json: options.restore || { episode_id: explorerEpisode, available: false, reason: "RESTORE_SOURCE_INCOMPATIBLE", plan: null } });
    }
    return route.fulfill({ status: 404, json: { error: "UNEXPECTED_MOCK_REQUEST" } });
  });
  return { writes, polls: () => polls, previews: () => previews };
}
