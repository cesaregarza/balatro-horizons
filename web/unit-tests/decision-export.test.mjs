import assert from "node:assert/strict";
import test from "node:test";

import { decisionExport } from "../.unit-dist/decisionExport.js";

const action = {
  decision: 31,
  event_id: "committed-event",
  action_number: 1,
  ante: 4,
  phase: "SHOP",
  type: "buy",
  note: 'A quoted "note"\n<script>example</script>',
  item: "Visible Joker",
  money_before: "10",
  money_after: "5",
  money_change: "-5",
};
const ledger = {
  source_journal_head: "verified-source-hash",
  manifest: {
    episode_id: "a".repeat(32),
    agent: "test",
    evidence_kind: "SYNTHETIC_TEST",
    evaluation_eligible: false,
  },
  summary: {
    outcome: "GAME_LOSS",
    reason: "VERIFIED_ENGINE_TERMINAL",
    cost_usd: 0,
  },
  actions: [action],
  uncommitted_actions: [
    {
      ...action,
      action_number: undefined,
      event_id: "rejected-event",
      status: "rejected",
      rejection_code: "INVALID_ACTION",
    },
    {
      ...action,
      decision: 32,
      action_number: undefined,
      event_id: "unresolved-event",
      status: "no_committed_transition",
    },
  ],
  rounds: [],
};

test("JSONL groups each decision once, keeps failed attempts and preserves strings", () => {
  const result = decisionExport(ledger, "jsonl", "2026-09-16T12:00:00Z");
  assert.equal(result.decisionCount, 2);
  assert.equal(result.content.endsWith("\n"), true);
  const lines = result.content
    .trimEnd()
    .split("\n")
    .map((line) => JSON.parse(line));
  assert.deepEqual(
    lines.map((line) => line.decision),
    [31, 32],
  );
  assert.equal(lines[0].committed_actions[0].note, action.note);
  assert.equal(
    lines[0].uncommitted_requests[0].rejection_code,
    "INVALID_ACTION",
  );
  assert.deepEqual(lines[1].committed_actions, []);
  assert.equal(
    lines[1].uncommitted_requests[0].status,
    "no_committed_transition",
  );
  assert.equal(result.content.includes("<script>"), false);
  for (const line of lines) {
    assert.equal(line.source_journal_head, ledger.source_journal_head);
    assert.equal(line.episode.evidence_kind, "SYNTHETIC_TEST");
    assert.equal(line.episode.evaluation_eligible, false);
  }
});

test("JSON and JSONL describe the same partial snapshot without mutating the ledger", () => {
  const partial = { ...ledger, summary: null };
  const before = JSON.stringify(partial);
  const json = decisionExport(partial, "json", "fixed-time");
  const jsonl = decisionExport(partial, "jsonl", "fixed-time");
  const document = JSON.parse(json.content);
  const lines = jsonl.content
    .trimEnd()
    .split("\n")
    .map((line) => JSON.parse(line));
  assert.equal(document.snapshot_status, "in_progress");
  assert.equal(document.run_summary, null);
  assert.equal(json.filename.includes("-partial.json"), true);
  assert.equal(jsonl.filename.includes("-partial.jsonl"), true);
  for (const [index, line] of lines.entries()) {
    const { decisions, ...metadata } = document;
    assert.deepEqual(line, { ...metadata, ...decisions[index] });
  }
  assert.equal(JSON.stringify(partial), before);
});

test("unexpected private and provider fields are not serialized", () => {
  const extra = {
    ...ledger,
    provider_response: { encrypted_content: "OPAQUE_SENTINEL" },
    manifest: {
      ...ledger.manifest,
      seed: "SEED_SENTINEL",
      credentials: "KEY_SENTINEL",
    },
    actions: [
      {
        ...action,
        raw_engine: "ENGINE_SENTINEL",
        memory_update: "MEMORY_SENTINEL",
      },
    ],
    summary: { ...ledger.summary, private_path: "PATH_SENTINEL" },
  };
  for (const format of ["json", "jsonl"]) {
    const exported = decisionExport(extra, format);
    assert.equal(exported.content.includes("SENTINEL"), false);
    assert.equal(exported.content.includes("omitted_content"), true);
  }
  const empty = { ...ledger, actions: [], uncommitted_actions: [] };
  assert.equal(decisionExport(empty, "jsonl").content, "");
  assert.deepEqual(JSON.parse(decisionExport(empty, "json").content).decisions, []);
});
