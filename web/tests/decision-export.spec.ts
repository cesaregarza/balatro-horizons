import { expect, test } from "@playwright/test";
import type { DecisionLedger, DecisionRow } from "../src/api";
import { decisionExport } from "../src/decisionExport";

const action: DecisionRow = {
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
const ledger: DecisionLedger = {
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
  expect(result.decisionCount).toBe(2);
  expect(result.content.endsWith("\n")).toBe(true);
  const lines = result.content
    .trimEnd()
    .split("\n")
    .map((line) => JSON.parse(line));
  expect(lines.map((line) => line.decision)).toEqual([31, 32]);
  expect(lines[0].committed_actions[0].note).toBe(action.note);
  expect(lines[0].uncommitted_requests[0].rejection_code).toBe(
    "INVALID_ACTION",
  );
  expect(lines[1].committed_actions).toEqual([]);
  expect(lines[1].uncommitted_requests[0].status).toBe(
    "no_committed_transition",
  );
  expect(result.content).not.toContain("<script>");
  for (const line of lines) {
    expect(line.source_journal_head).toBe(ledger.source_journal_head);
    expect(line.episode.evidence_kind).toBe("SYNTHETIC_TEST");
    expect(line.episode.evaluation_eligible).toBe(false);
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
  expect(document.snapshot_status).toBe("in_progress");
  expect(document.run_summary).toBe(null);
  expect(json.filename).toContain("-partial.json");
  expect(jsonl.filename).toContain("-partial.jsonl");
  for (const [index, line] of lines.entries()) {
    const { decisions, ...metadata } = document;
    expect(line).toEqual({ ...metadata, ...decisions[index] });
  }
  expect(JSON.stringify(partial)).toBe(before);
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
    summary: { ...ledger.summary!, private_path: "PATH_SENTINEL" },
  };
  for (const format of ["json", "jsonl"] as const) {
    const exported = decisionExport(extra, format);
    expect(exported.content).not.toContain("SENTINEL");
    expect(exported.content).toContain("omitted_content");
  }
  const empty = { ...ledger, actions: [], uncommitted_actions: [] };
  expect(decisionExport(empty, "jsonl").content).toBe("");
  expect(JSON.parse(decisionExport(empty, "json").content).decisions).toEqual(
    [],
  );
});
