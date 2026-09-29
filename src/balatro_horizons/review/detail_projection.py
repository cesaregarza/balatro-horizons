"""Small ordinary explorer response; exact technical records remain opt-in."""


def reasoning_payload(event):
    body = event.get("payload", {}).get("body", {})
    output = body.get("output") if isinstance(body, dict) else None
    summaries = []
    for item in output if isinstance(output, list) else []:
        if not isinstance(item, dict) or item.get("type") != "reasoning":
            continue
        parts = item.get("summary")
        if isinstance(parts, list):
            summaries.extend({"type": "summary_text", "text": part["text"]}
                             for part in parts if isinstance(part, dict)
                             and part.get("type") == "summary_text"
                             and isinstance(part.get("text"), str))
    return {"body": {"output": [{"type": "reasoning", "summary": summaries}]}}


def ordinary_events(records):
    """Display-only projection of already verified records, never a new journal.

    Keep every sequence slot for the existing decision-segment boundaries, but
    only board, terminal and returned-reasoning payloads needed by ordinary detail.
    Exact records and ledger/export accounting always use the full snapshot.
    """
    return [{**event, "payload": (
        event["payload"] if event["type"] in {"observation", "terminal"}
        else reasoning_payload(event) if event["type"] == "provider_response" else {}
    )} for event in records]


def compact_detail(view):
    responses = [{"type": "provider_response", "event_id": event["event_id"],
                  "payload": reasoning_payload(event)}
                 for event in view.get("action_events", [])
                 if event["type"] == "provider_response"]
    return {**view, "action_events": responses, "technical_records_included": False}
