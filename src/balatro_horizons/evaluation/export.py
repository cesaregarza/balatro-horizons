"""Public episode export assembly kept separate from report rendering."""

from typing import Any

from balatro_horizons.contracts import ActionEnvelope, Observation
from balatro_horizons.evaluation import privacy

PUBLIC_MANIFEST_KEYS = (
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
    "agent_protocol",
    "certificate_id",
    "budget_extension",
)
PUBLIC_EVENT_TYPES = (
    "agent_context",
    "agent_operation",
    "provider_request",
    "provider_response",
    "helper_result",
    "run_note",
    "action_rejected",
    "provider_input_check",
    "harness_failure",
)


def public_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    return {key: manifest[key] for key in PUBLIC_MANIFEST_KEYS if key in manifest}


def export_events(store: Any, eid: str) -> list[dict[str, Any]]:
    events = []
    for event in store.events(eid):
        kind = event["type"]
        if kind == "observation":
            payload = Observation.model_validate(event["payload"]).model_dump(mode="json")
        elif kind == "action_commit":
            payload = ActionEnvelope.model_validate(event["payload"]).model_dump(mode="json")
        elif kind == "terminal":
            payload = store.summary(eid)
        elif kind in PUBLIC_EVENT_TYPES:
            payload = event["payload"]
        else:
            continue
        events.append(
            {
                "event_id": event["event_id"],
                "sequence": event["sequence"],
                "type": kind,
                "observation_id": event["observation_id"],
                "payload": privacy.public_provider_payload(payload),
            }
        )
    return events


def export_annotations(store: Any, eid: str) -> list[dict[str, Any]]:
    from balatro_horizons.review.service import ReviewService

    return [
        {key: value for key, value in row.items() if key != "reviewer_id"}
        for row in ReviewService(store).annotations(eid)
    ]


def episode_export(
    store: Any,
    eid: str,
) -> dict[str, Any]:
    manifest = store.manifest(eid)
    events = export_events(store, eid)
    annotations = export_annotations(store, eid)
    agent_protocol = _agent_protocol(store, eid)
    summary = store.summary(eid)
    private = store.manifest(eid, True)
    result = {
        "agent_protocol": agent_protocol,
        "annotations": annotations,
        "export_policy": "public-schema-v1",
        "manifest": public_manifest(manifest),
        "events": events,
        "summary": summary,
    }
    privacy.scan(result, [private.get("seed")])
    from balatro_horizons.review.service import ReviewService

    max_event_seen = len(store.events(eid)) - 1
    ReviewService(store).expose(
        eid,
        "public_export",
        outcome_seen=True,
        model_identity_seen=True,
        max_event_seen=max_event_seen,
    )
    return result


def _agent_protocol(store: Any, eid: str) -> Any:
    return next(
        (
            event["payload"].get("agent_protocol")
            for event in store.events(eid)
            if event["type"] == "episode_start"
        ),
        None,
    )
