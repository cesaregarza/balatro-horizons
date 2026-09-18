import type { DecisionLedger, DecisionRow } from "./api";

export type DecisionExportFormat = "json" | "jsonl";

const rowFields = [
  "event_id",
  "decision",
  "action_number",
  "ante",
  "blind",
  "phase",
  "type",
  "note",
  "item",
  "item_kind",
  "listed_price",
  "effects",
  "mode",
  "cards",
  "targets",
  "area",
  "ordered_objects",
  "ordering_before",
  "hand_types",
  "score",
  "total_chips",
  "target_before",
  "target_after",
  "hands_after",
  "discards_after",
  "money_before",
  "money_after",
  "money_change",
  "jokers_after",
  "jokers_added",
  "jokers_removed",
  "status",
  "rejection_code",
] as const;

function pick(value: object, fields: readonly string[]) {
  const record = value as Record<string, unknown>;
  return Object.fromEntries(
    fields
      .filter((field) => record[field] !== undefined)
      .map((field) => [field, record[field]]),
  );
}

function encode(value: unknown, pretty = false) {
  // Plain JSON downloads remain safe if subsequently embedded in an HTML report.
  return JSON.stringify(value, null, pretty ? 2 : undefined).replace(
    /[<>&\u2028\u2029]/g,
    (character) =>
      `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`,
  );
}

export function decisionExport(
  ledger: DecisionLedger,
  format: DecisionExportFormat,
  exportedAt = new Date().toISOString(),
) {
  // Input is the existing server-projected, privacy-scanned retrospective ledger.
  // Do not use the detail endpoint: its owner-only provider records are not an
  // export source. No network requests, game controls or review cursor changes.
  const model = ledger.manifest.config?.models?.[ledger.manifest.agent];
  const episode = {
    ...pick(ledger.manifest, [
      "episode_id",
      "created_at",
      "agent",
      "evidence_kind",
      "evaluation_eligible",
      "fixture",
      "validation_purpose",
      "parent_episode_id",
      "parent_decision",
      "assistance",
      "batch_id",
      "slot_id",
    ]),
    deck: ledger.manifest.config?.deck ?? null,
    stake: ledger.manifest.config?.stake ?? null,
    model: model
      ? pick(model, [
          "provider",
          "model",
          "settings",
          "pricing_date",
          "input_usd_per_million",
          "output_usd_per_million",
          "cached_input_usd_per_million",
          "cache_write_input_usd_per_million",
        ])
      : null,
  };
  const metadata = {
    schema_version: 1,
    export_kind: "decision_summaries",
    episode,
    exported_at: exportedAt,
    source_journal_head: ledger.source_journal_head,
    snapshot_status: ledger.summary?.outcome ? "finished" : "in_progress",
    run_summary: ledger.summary
      ? pick(ledger.summary, [
          "outcome",
          "reason",
          "cost_usd",
          "attempted_actions",
          "committed_actions",
          "provider_calls",
          "agent_protocol",
          "terminal_event_id",
          "journal_head",
        ])
      : null,
    omitted_content: [
      "full board observations and exact action envelopes",
      "provider prompts, outputs, and opaque continuation data",
      "helper results and agent memory",
      "annotations, private seeds, engine state, and credentials",
    ],
  };
  type Decision = {
    decision: number;
    committed_actions: Record<string, unknown>[];
    uncommitted_requests: Record<string, unknown>[];
  };
  const decisions = new Map<number, Decision>();
  function collect(
    rows: DecisionRow[],
    kind: "committed_actions" | "uncommitted_requests",
  ) {
    for (const row of rows) {
      if (!decisions.has(row.decision))
        decisions.set(row.decision, {
          decision: row.decision,
          committed_actions: [],
          uncommitted_requests: [],
        });
      decisions.get(row.decision)![kind].push(pick(row, rowFields));
    }
  }
  collect(ledger.actions, "committed_actions");
  collect(ledger.uncommitted_actions || [], "uncommitted_requests");
  const ordered = [...decisions.values()].sort(
    (a, b) => a.decision - b.decision,
  );
  const content =
    format === "json"
      ? encode({ ...metadata, decisions: ordered }, true) + "\n"
      : ordered
          .map((decision) => encode({ ...metadata, ...decision }) + "\n")
          .join("");
  const partial = metadata.snapshot_status === "in_progress" ? "-partial" : "";
  return {
    content,
    filename: `balatro-${ledger.manifest.episode_id}-decisions${partial}.${format}`,
    mime:
      format === "json"
        ? "application/json;charset=utf-8"
        : "application/x-ndjson;charset=utf-8",
    decisionCount: ordered.length,
  };
}

export function downloadDecisions(
  ledger: DecisionLedger,
  format: DecisionExportFormat,
) {
  const result = decisionExport(ledger, format);
  const url = URL.createObjectURL(
    new Blob([result.content], { type: result.mime }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = result.filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
