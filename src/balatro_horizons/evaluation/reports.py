"""Schema-selected export, privacy scan, and standalone escaped reports."""

import csv
import html
import json
from pathlib import Path

from balatro_horizons.evaluation.batches import summarize
from balatro_horizons.evaluation.export import episode_export as assemble_episode_export
from balatro_horizons.evaluation.privacy import public_provider_payload as public_provider_payload
from balatro_horizons.evaluation.privacy import scan
from balatro_horizons.harness.money import batch_attempts, reconcile_stop
from balatro_horizons.storage.journal import atomic_json, digest, identifier


def episode_export(store, eid):
    return assemble_episode_export(
        store,
        eid,
    )


def report_batch(store, bid, output):
    plan = json.loads((store.root / "batches" / identifier(bid) / "plan.json").read_text())
    attempts = batch_attempts(store, plan)
    report = {**summarize(plan, attempts), "scheduling_stop": reconcile_stop(store, plan, attempts)}
    from balatro_horizons.review.service import ReviewService

    review = ReviewService(store)
    for row in attempts:
        if row["manifest"].get("batch_id") == bid:
            review.expose(
                row["episode_id"],
                "batch_report",
                outcome_seen=True,
                model_identity_seen=True,
                max_event_seen=len(store.events(row["episode_id"])) - 1,
            )
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output / "report.json", report)
    fields = [
        "agent",
        "planned",
        "attempts",
        "valid",
        "wins",
        "unresolved",
        "win_rate",
        "coverage",
        "all_attempt_cost_usd",
    ]
    with (output / "report.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for agent, data in report["agents"].items():
            writer.writerow({k: agent if k == "agent" else data[k] for k in fields})
    body = "<h1>Balatro Horizons</h1><p>Autonomous outcomes with coverage and complete attempt costs. Human-assisted branches are excluded.</p>"
    body += "<pre>" + html.escape(json.dumps(report, indent=2)) + "</pre>"
    (output / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Balatro Horizons report</title><style>body{max-width:1000px;margin:3rem auto;padding:0 1rem;font:16px system-ui;background:#101923;color:#edf3f8}pre{white-space:pre-wrap}</style>'
        + body
    )
    return report


def export_batch(store, bid, output):
    plan = json.loads((store.root / "batches" / identifier(bid) / "plan.json").read_text())
    private = json.loads((store.root / "batches" / bid / "private.json").read_text())
    attempts = batch_attempts(store, plan)
    episodes = [episode_export(store, e["episode_id"]) for e in attempts]
    bundle = {
        "schema_version": "1.0",
        "export_policy": "public-schema-v1",
        "plan": plan,
        "episodes": episodes,
        "report": {
            **summarize(plan, attempts),
            "scheduling_stop": reconcile_stop(store, plan, attempts),
        },
    }
    scan(bundle, private["seed_by_group"].values())
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output / "public.json", bundle, immutable=True)
    return {"episodes": len(episodes), "sha256": digest(bundle), "policy": "public-schema-v1"}
