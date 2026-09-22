"""Minimal public inputs for the episode-only review ledger."""

from balatro_horizons.contracts import ActionEnvelope, Observation

MANIFEST_FIELDS = (
    "schema_version", "episode_id", "created_at", "evidence_kind", "agent", "config",
    "evaluation_eligible", "fixture", "validation_purpose", "parent_episode_id",
    "parent_decision", "assistance", "batch_id", "slot_id", "certificate_id", "budget_extension",
)
EVENT_FIELDS = ("event_id", "sequence", "type", "observation_id", "request_id", "actor")


def manifest_projection(manifest):
    return {key: manifest[key] for key in MANIFEST_FIELDS if key in manifest}


def payload_projection(kind, payload):
    if kind == "observation":
        return Observation.model_validate(payload).model_dump(mode="json")
    if kind in ("action_commit", "action_intent"):
        return ActionEnvelope.model_validate(payload).model_dump(mode="json")
    if kind == "provider_request":
        return {"body": {"tools": [
            {"name": tool.get("name")} for tool in payload["body"].get("tools", [])
        ]}}
    if kind == "provider_response":
        body = payload.get("body")
        usage = body.get("usage") if isinstance(body, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        return {"body": {"usage": {
            key: usage[key] for key in ("input_tokens", "output_tokens") if key in usage
        }}}
    if kind == "helper_result":
        return {"operation": payload["operation"]}
    if kind == "action_rejected":
        return {"code": payload["code"]}
    if kind == "agent_context":
        # Helper-only decisions need the event's presence, not its large context.
        return {}
    return None


def event_projection(records):
    events = []
    for event in records:
        payload = payload_projection(event["type"], event["payload"])
        if payload is not None:
            events.append({**{key: event.get(key) for key in EVENT_FIELDS}, "payload": payload})
    return events
