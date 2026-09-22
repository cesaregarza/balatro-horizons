"""Server-owned decision export projection.

The browser receives a download response only; privacy-sensitive field
selection remains next to the existing report scan on the server.
"""

import json
from datetime import UTC, datetime

from starlette.responses import Response

from balatro_horizons.evaluation.reports import scan

ROW_FIELDS = (
    "event_id", "decision", "action_number", "ante", "blind", "phase", "type", "note",
    "item", "item_kind", "listed_price", "effects", "mode", "cards", "targets", "area",
    "ordered_objects", "ordering_before", "hand_types", "score", "total_chips",
    "target_before", "target_after", "hands_after", "discards_after", "money_before",
    "money_after", "money_change", "jokers_after", "jokers_added", "jokers_removed",
    "status", "rejection_code",
)
MANIFEST_FIELDS = (
    "episode_id", "created_at", "agent", "evidence_kind", "evaluation_eligible", "fixture",
    "validation_purpose", "parent_episode_id", "parent_decision", "assistance", "batch_id",
    "slot_id", "certificate_id", "budget_extension",
)
SUMMARY_FIELDS = (
    "outcome", "reason", "cost_usd", "attempted_actions", "committed_actions", "provider_calls",
    "agent_protocol", "terminal_event_id", "journal_head",
)
ACCOUNTING_FIELDS = (
    "ledger_scope", "own_committed_actions", "inherited_committed_actions",
    "total_committed_actions", "terminal_count_scope",
)


def safe_json(value, **kwargs):
    """Serialize JSON safely for downloads embedded in browser contexts."""
    return (
        json.dumps(value, **kwargs)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def pick(value, fields):
    return {field: value[field] for field in fields if field in value and value[field] is not None}


def metadata(ledger, exported_at):
    manifest = ledger["manifest"]
    model = manifest.get("config", {}).get("models", {}).get(manifest.get("agent"))
    episode = {
        **pick(manifest, MANIFEST_FIELDS),
        "deck": manifest.get("config", {}).get("deck"),
        "stake": manifest.get("config", {}).get("stake"),
        "model": pick(
            model,
            (
                "provider", "model", "settings", "pricing_date", "input_usd_per_million",
                "output_usd_per_million", "cached_input_usd_per_million",
                "cache_write_input_usd_per_million",
            ),
        ) if model else None,
    }
    summary = ledger.get("summary")
    return {
        "schema_version": 1,
        "export_kind": "decision_summaries",
        "episode": episode,
        "exported_at": exported_at,
        "source_journal_head": ledger.get("source_journal_head"),
        "snapshot_status": "finished" if summary and summary.get("outcome") else "in_progress",
        "run_summary": pick(summary, SUMMARY_FIELDS) if summary else None,
        "action_accounting": pick(ledger.get("action_accounting") or {}, ACCOUNTING_FIELDS),
        **({"cost_accounting": "Runner provider calls are inherited-inclusive; cost is own-only."}
           if manifest.get("budget_extension") else {}),
        "omitted_content": [
            "full board observations and exact action envelopes",
            "provider prompts, outputs, and opaque continuation data",
            "helper results and agent memory",
            "annotations, private seeds, engine state, and credentials",
        ],
    }


def grouped_decisions(ledger):
    decisions = {}
    for key, rows in (
        ("committed_actions", ledger.get("actions", [])),
        ("uncommitted_requests", ledger.get("uncommitted_actions") or []),
    ):
        for row in rows:
            item = decisions.setdefault(
                row["decision"],
                {
                    "decision": row["decision"],
                    "committed_actions": [],
                    "uncommitted_requests": [],
                },
            )
            item[key].append(pick(row, ROW_FIELDS))
    return [decisions[key] for key in sorted(decisions)]


def export_content(ledger, format, exported_at=None):
    if format not in ("json", "jsonl"):
        raise ValueError("UNKNOWN_EXPORT_FORMAT")
    metadata_row = metadata(ledger, exported_at or datetime.now(UTC).isoformat())
    decisions = grouped_decisions(ledger)
    if format == "json":
        return safe_json(
            {**metadata_row, "decisions": decisions}, indent=2, ensure_ascii=False
        ) + "\n"
    return "".join(
        safe_json({**metadata_row, **decision}, ensure_ascii=False) + "\n"
        for decision in decisions
    )


def export_response(ledger, format):
    content = export_content(ledger, format)
    scan(content)
    episode_id = ledger["manifest"]["episode_id"]
    summary = ledger.get("summary") or {}
    suffix = "-partial" if not summary.get("outcome") else ""
    media = "application/json" if format == "json" else "application/x-ndjson"
    filename = f"balatro-{episode_id}-decisions{suffix}.{format}"
    return Response(
        content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
