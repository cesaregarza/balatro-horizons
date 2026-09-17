"""Selected-decision diagnostics from public journals; never executes a model or game."""

import json

from balatro_horizons.evaluation.reports import public_provider_payload, scan
from balatro_horizons.storage.journal import locked

EVENTS = frozenset({
    "agent_context", "agent_operation", "provider_input_check", "provider_request",
    "provider_response", "provider_error", "helper_result", "run_note", "action_intent",
    "action_commit", "action_rejected", "harness_failure", "terminal",
})


def parsed(value):
    if not isinstance(value, str):
        return value, False
    try:
        return json.loads(value), False
    except (ValueError, TypeError):
        return value, True


def tools_returned(body):
    """Keep every returned call, including malformed or multiple operations."""
    result = []
    if not isinstance(body, dict):
        return result
    blocks = body.get("output", body.get("content", []))
    for block in blocks if isinstance(blocks, list) else []:
        if not isinstance(block, dict) or block.get("type") not in ("function_call", "tool_use"):
            continue
        raw = block.get("arguments") if block["type"] == "function_call" else block.get("input")
        arguments, invalid = parsed(raw)
        result.append({
            "call_id": block.get("call_id", block.get("id")), "name": block.get("name"),
            "arguments": arguments, "raw_arguments": raw, "arguments_parse_error": invalid,
            "delivered_results": [],
        })
    return result


def delivered_results(body):
    """Provider-native call IDs establish which result was sent back to a tool."""
    if not isinstance(body, dict):
        return
    messages = body.get("input", body.get("messages", []))
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict):
            continue
        if message.get("type") == "function_call_output":
            yield message.get("call_id"), message.get("output")
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    yield block.get("tool_use_id"), block.get("content")


def project(start, segment, transition=None, *, complete=False):
    events = [public_provider_payload(event) for event in segment if event["type"] in EVENTS]
    calls, by_request, current, context = [], {}, None, None
    for event in events:
        kind, payload = event["type"], event["payload"]
        if kind == "agent_context":
            context = event
        elif kind == "provider_request":
            current = {
                "request_id": event["request_id"], "request_event": event,
                "context_event_id": context["event_id"] if context else None,
                "response_event": None, "error_event": None,
                "status": "waiting_for_response", "tools": [], "journal_events": [],
            }
            calls.append(current)
            by_request[event["request_id"]] = current
        elif kind in ("provider_response", "provider_error"):
            call = by_request.get(event.get("request_id"))
            if call is not None:
                call["response_event" if kind == "provider_response" else "error_event"] = event
                call["status"] = "response_received" if kind == "provider_response" else "transport_error"
                if kind == "provider_response":
                    call["tools"] = tools_returned(payload.get("body", {}))
        elif current is not None and kind in {
            "agent_operation", "helper_result", "run_note", "action_intent", "action_commit",
            "action_rejected", "harness_failure",
        }:
            # These records have no provider call ID. Label this association as
            # chronological; never fabricate IDs or per-tool execution for multiple calls.
            current["journal_events"].append(event)
            if kind in ("helper_result", "action_commit", "action_rejected", "harness_failure"):
                current["status"] = kind
    # Calls may repeat a historical result on later requests. Deduplicate by call ID
    # and payload, while keeping all actual requests available for exact inspection.
    known = {}
    for call in calls:
        for identifier, value in delivered_results(call["request_event"]["payload"].get("body", {})):
            tool = known.get(identifier)
            if tool is not None:
                value, invalid = parsed(value)
                item = {"content": value, "content_parse_error": invalid,
                        "request_id": call["request_id"]}
                if not any(row["content"] == value for row in tool["delivered_results"]):
                    tool["delivered_results"].append(item)
        for tool in call["tools"]:
            if tool["call_id"]:
                known[tool["call_id"]] = tool
        if complete and call["status"] == "waiting_for_response":
            call["status"] = "no_recorded_response"
    return {
        "schema_version": "decision-dev-trace-v1", "episode_id": start["episode_id"],
        "decision": start["observation_id"], "complete": complete,
        "observation": start["payload"], "transition": transition["payload"] if transition else None,
        "calls": calls, "events": events,
        "source_journal_head": (segment[-1] if segment else start)["hash"],
        "omissions": ["Opaque provider continuations are omitted; returned summaries are not hidden internal reasoning.",
                      "Private engine state, checkpoints, credentials and seeds are not inputs to this view."],
        "linkage": "Delivered results use provider call IDs. Journal results are associated by request order within this decision.",
    }


def decision_trace(review, token, decision):
    from balatro_horizons.review.service import ReviewError

    _, session = review.session(token)
    if session["mode"] != "retrospective":
        raise ReviewError("RETROSPECTIVE_REVIEW_REQUIRED")
    store, eid = review.store, session["episode_id"]
    with locked(store.episode_path(eid) / ".writer.lock"):
        records = store.events(eid)
    start = next((e for e in records if e["type"] == "observation" and e["observation_id"] == decision), None)
    if start is None:
        raise ReviewError("DECISION_NOT_AVAILABLE")
    transition = next((e for e in records[start["sequence"] + 1:] if e["type"] == "observation"), None)
    end = transition["sequence"] if transition else len(records)
    segment = records[start["sequence"] + 1:end]
    result = project(start, segment, transition,
                     complete=bool(transition or any(e["type"] == "terminal" for e in segment)))
    # Only the private seed is consulted to reject accidental text leakage; it is
    # never copied into the projection. No checkpoint or raw engine file is read.
    scan(result, [store.manifest(eid, True).get("seed")])
    review.expose(eid, "dev_trace", model_identity_seen=True,
                  max_event_seen=transition["sequence"] if transition else end - 1,
                  outcome_seen=any(e["type"] == "terminal" for e in segment))
    return result
