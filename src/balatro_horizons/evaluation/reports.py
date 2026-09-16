"""Schema-selected export, privacy scan, and standalone escaped reports."""

import csv
import html
import json
import re
from pathlib import Path

from balatro_horizons.contracts import ActionEnvelope, Observation
from balatro_horizons.evaluation.batches import summarize
from balatro_horizons.evaluation.scheduling import batch_attempts, reconcile_stop
from balatro_horizons.storage.journal import atomic_json, digest, identifier

# Require a drive-letter boundary so a public https:// link is not treated as s:/.
FORBIDDEN = re.compile(
    r"(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9_-]+|/mnt/|/root/|(?<![A-Za-z0-9+.-])[A-Za-z]:[\\/]|rng_state|hidden_draw_order|PRIVATE KEY)"
)


def scan(value, secrets=()):
    serialized = json.dumps(value, ensure_ascii=False)
    if FORBIDDEN.search(serialized) or any(secret and secret in serialized for secret in secrets):
        raise ValueError("EXPORT_PRIVACY_SCAN_FAILED")


def public_provider_payload(value):
    """Remove opaque continuation material while retaining inspectable public output."""
    if isinstance(value, list):
        return [public_provider_payload(item) for item in value]
    if not isinstance(value, dict):
        return value
    block_type = value.get("type")
    result = {
        key: public_provider_payload(item)
        for key, item in value.items()
        if key != "encrypted_content"
        and not (block_type in ("thinking", "redacted_thinking") and key == "signature")
        and not (block_type == "redacted_thinking" and key == "data")
    }
    omitted = (
        "encrypted_content" in value
        or (block_type in ("thinking", "redacted_thinking") and "signature" in value)
        or (block_type == "redacted_thinking" and "data" in value)
    )
    if omitted:
        result["opaque_continuation_omitted"] = True
    return result


def episode_export(store, eid):
    manifest = store.manifest(eid)
    public_manifest = {
        k: manifest[k]
        for k in (
            "schema_version",
            "episode_id",
            "created_at",
            "evidence_kind",
            "agent",
            "config",
            "evaluation_eligible",
            "fixture",
            "validation_purpose",
            "parent_episode_id",
            "parent_decision",
            "assistance",
            "batch_id",
            "slot_id",
        )
        if k in manifest
    }
    events = []
    for event in store.events(eid):
        kind = event["type"]
        if kind == "observation":
            payload = Observation.model_validate(event["payload"]).model_dump(mode="json")
        elif kind == "action_commit":
            payload = ActionEnvelope.model_validate(event["payload"]).model_dump(mode="json")
        elif kind == "terminal":
            payload = store.summary(eid)
        elif kind in (
            "agent_context",
            "agent_operation",
            "provider_request",
            "provider_response",
            "helper_result",
            "action_rejected",
        ):
            payload = event["payload"]
        else:
            continue
        payload = public_provider_payload(payload)
        events.append(
            {
                "event_id": event["event_id"],
                "sequence": event["sequence"],
                "type": kind,
                "observation_id": event["observation_id"],
                "payload": payload,
            }
        )
    from balatro_horizons.review.service import ReviewService

    annotations = [
        {k: v for k, v in row.items() if k != "reviewer_id"}
        for row in ReviewService(store).annotations(eid)
    ]
    result = {
        "annotations": annotations,
        "export_policy": "public-schema-v1",
        "manifest": public_manifest,
        "events": events,
        "summary": store.summary(eid),
    }
    private = store.manifest(eid, True)
    scan(result, [private.get("seed")])
    ReviewService(store).expose(
        eid,
        "public_export",
        outcome_seen=True,
        model_identity_seen=True,
        max_event_seen=len(store.events(eid)) - 1,
    )
    return result


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
