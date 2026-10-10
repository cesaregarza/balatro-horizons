"""Small ordinary explorer response; exact technical records remain opt-in."""

from balatro_horizons.review.provider_projection import response_projection


def reasoning_payload(event):
    payload = response_projection(event)["payload"]
    reasoning = payload["returned_reasoning"]
    summaries = [{"type": "summary_text", "text": text} for text in reasoning["texts"]]
    return {"body": {"output": [{"type": "reasoning", "summary": summaries}]},
            "reported_usage": payload["reported_usage"], "returned_reasoning": reasoning}


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
