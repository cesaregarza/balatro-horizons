#!/usr/bin/env python3
"""Aggregate verified public transcripts offline; never emit opaque response content."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

from balatro_horizons.review.service import ReviewService
from balatro_horizons.storage.journal import Store


def size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def stats(values):
    return {"min": min(values), "median": median(values), "max": max(values), "sum": sum(values)} if values else {}


def diagnostic_fields(response):
    if response is None:
        return None
    diagnostics = response["payload"]["body"].get("prompt_cache_diagnostics")
    if not isinstance(diagnostics, dict):
        return None
    fields = ("type", "reason", "comparison_reusable_tokens", "cache_missed_tokens")
    return {key: diagnostics[key] for key in fields if key in diagnostics}


def input_detail_tokens(usage, field):
    details = usage.get("input_tokens_details")
    if not isinstance(details, dict):
        return 0
    value = details.get(field, 0)
    return value if type(value) is int and value >= 0 else 0


def request_parts(body):
    """Understand both top-level instructions and the v4 developer prefix."""
    messages = body.get("input", body.get("messages", []))
    instructions = body.get("instructions", body.get("system", ""))
    if messages and messages[0].get("role") == "developer":
        instructions = "\n".join(block["text"] for block in messages[0]["content"] if block.get("type") == "input_text")
        messages = messages[1:]
    return instructions, messages


def continuation_diagnostics(events):
    """Check recorded OpenAI helper round trips without emitting opaque items."""
    requests = [e for e in events if e["type"] == "provider_request"]
    responses = {e["request_id"]: e for e in events if e["type"] == "provider_response"}
    result = {
        "provider": "openai",
        "helper_round_trips_checked": 0,
        "exact_turn_round_trips": 0,
        "encrypted_reasoning_items_preserved": 0,
        "matched_tool_results": 0,
        "pending_helper_followups": 0,
        "decision_starts_checked": 0,
        "failures": [],
    }
    seen = set()
    for request in requests:
        body = request["payload"]["body"]
        if "reasoning.encrypted_content" not in body.get("include", []):
            continue
        decision = request["observation_id"]
        if decision in seen:
            continue
        seen.add(decision)
        result["decision_starts_checked"] += 1
        if any(item.get("type") in ("reasoning", "function_call", "function_call_output")
               or item.get("role") == "assistant" for item in body.get("input", [])):
            result["failures"].append({"sequence": request["sequence"], "code": "DECISION_RESET_FAILED"})
    for helper in (e for e in events if e["type"] == "helper_result"):
        decision, sequence = helper["observation_id"], helper["sequence"]
        prior_request = next((e for e in reversed(requests)
                              if e["observation_id"] == decision and e["sequence"] < sequence), None)
        prior = responses.get(prior_request["request_id"]) if prior_request else None
        following = next((e for e in requests
                          if e["observation_id"] == decision and e["sequence"] > sequence), None)
        if not following:
            result["pending_helper_followups"] += 1
            continue
        body = following["payload"]["body"]
        if "reasoning.encrypted_content" not in body.get("include", []):
            continue
        result["helper_round_trips_checked"] += 1
        items = prior["payload"]["body"].get("output", []) if prior else []
        delivered = body.get("input", [])
        exact = bool(items) and any(delivered[i:i + len(items)] == items
                                    for i in range(len(delivered)))
        if exact:
            result["exact_turn_round_trips"] += 1
            result["encrypted_reasoning_items_preserved"] += sum(
                item.get("type") == "reasoning" and bool(item.get("encrypted_content"))
                for item in items
            )
        else:
            result["failures"].append({"sequence": sequence, "code": "PROVIDER_TURN_CHANGED_OR_MISSING"})
        calls = [item for item in items if item.get("type") == "function_call"]
        for call in calls:
            output = next((item.get("output") for item in delivered
                           if item.get("type") == "function_call_output"
                           and item.get("call_id") == call.get("call_id")), None)
            try:
                matches = json.loads(output) == helper["payload"]["result"]
            except (ValueError, TypeError):
                matches = False
            if matches:
                result["matched_tool_results"] += 1
            else:
                result["failures"].append({"sequence": sequence, "code": "TOOL_RESULT_CHANGED_OR_MISSING"})
        if not calls:
            result["failures"].append({"sequence": sequence, "code": "MISSING_HELPER_CALL"})
    return result


def audit(store, eid, expose=False):
    events = store.events(eid)
    if expose:
        ReviewService(store).expose(eid, "transcript_efficiency_audit_2026_09_15", max_event_seen=len(events)-1, outcome_seen=True, model_identity_seen=True)
    manifest = store.manifest(eid)
    requests = [e for e in events if e["type"] == "provider_request"]
    responses = {e["request_id"]: e for e in events if e["type"] == "provider_response"}
    obs = {e["observation_id"]: e for e in events if e["type"] == "observation"}
    components = defaultdict(list)
    component_values = defaultdict(list)
    rows = []
    for request in requests:
        body = request["payload"]["body"]
        instructions, messages = request_parts(body)
        view = json.loads(messages[0]["content"])["observation"]
        values = {"instructions": instructions, "tools": body["tools"], "observation": view, "helper_messages": messages[1:], "request_body": body}
        for field in ("memory", "recent_public_events", "action_constraints", "presentation", "remaining_budget", "retrieval_context"):
            values[field] = view.get(field)
        for field, value in view["state"].items():
            values["state." + field] = value
        for key, value in values.items():
            components[key].append(size(value))
            component_values[key].append(json.dumps(value, sort_keys=True, ensure_ascii=False))
        response = responses.get(request["request_id"])
        usage = response["payload"]["body"].get("usage", {}) if response else {}
        rows.append({"decision": request["observation_id"], "request_sequence": request["sequence"], "response_sequence": response["sequence"] if response else None, "phase": view["phase"], "usage": usage, "prompt_cache_diagnostics": diagnostic_fields(response), "recorded_cost_usd": response["payload"]["cost_usd"] if response else None, "reserved_usd": request["payload"]["reserved_usd"], "component_bytes": {key: size(value) for key, value in values.items()}, "tool_names": [t["name"] for t in body["tools"]], "helper_message_count": len(messages)-1, "cleared": view.get("retrieval_context", {}).get("cleared", []), "memory_characters": len(view.get("memory", "")), "history_count": len(view.get("recent_public_events", []))})
    usages = [r["usage"] for r in rows]
    input_total = sum(u.get("input_tokens", 0) for u in usages)
    output_total = sum(u.get("output_tokens", 0) for u in usages)
    cached_total = sum(input_detail_tokens(u, "cached_tokens") for u in usages)
    cache_write_total = sum(input_detail_tokens(u, "cache_write_tokens") for u in usages)
    diagnostic_counts = Counter(
        (row["prompt_cache_diagnostics"] or {}).get("type", "missing") for row in rows
    )
    reasoning_total = sum(u.get("output_tokens_details", {}).get("reasoning_tokens", 0) for u in usages)
    operations = [e for e in events if e["type"] == "agent_operation"]
    helpers = [e for e in events if e["type"] == "helper_result"]
    model = manifest["config"]["models"][manifest["agent"]]
    return {"episode_id": eid, "journal_integrity": "passed", "journal_head": events[-1]["hash"], "event_count": len(events), "model": model, "skills": manifest["config"].get("skills"), "limits": manifest["config"]["budgets"], "terminal": next((e["payload"] for e in reversed(events) if e["type"] == "terminal"), None), "request_count": len(requests), "response_count": len(responses), "prompt_cache_diagnostics": dict(diagnostic_counts), "action_count": sum(e["type"] == "action_commit" for e in events), "operation_counts": dict(Counter(e["payload"]["operation"]["kind"] for e in operations)), "action_counts": dict(Counter(e["payload"]["operation"].get("envelope", {}).get("action", {}).get("type", "helper") for e in operations)), "helpers": [{"sequence": e["sequence"], "decision": e["observation_id"], "operation": e["payload"]["operation"], "result_bytes": size(e["payload"]["result"]), "complete": e["payload"]["result"].get("complete"), "next_offset": e["payload"]["result"].get("next_offset")} for e in helpers], "rejections": [{"sequence": e["sequence"], "decision": e["observation_id"], "code": e["payload"]["code"]} for e in events if e["type"] == "action_rejected"], "usage": {"input_tokens": input_total, "output_tokens": output_total, "cached_input_tokens": cached_total, "cache_write_input_tokens": cache_write_total, "reasoning_tokens": reasoning_total, "input_per_call": stats([u.get("input_tokens", 0) for u in usages]), "output_per_call": stats([u.get("output_tokens", 0) for u in usages]), "reasoning_per_call": stats([u.get("output_tokens_details", {}).get("reasoning_tokens", 0) for u in usages])}, "recorded_cost_usd": sum(r["recorded_cost_usd"] or 0 for r in rows), "unresolved_reservations_usd": sum(r["reserved_usd"] for r in rows if r["response_sequence"] is None), "component_bytes": {k: stats(v) for k, v in components.items()}, "component_unique_values": {k: len(set(v)) for k, v in component_values.items()}, "rows": rows, "final_public_resources": list(obs.values())[-1]["payload"]["state"]["resources"], "final_public_progress": list(obs.values())[-1]["payload"]["state"]["progress"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--episode-id", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--record-exposure", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output.resolve()
    if root.is_relative_to("/mnt") or output.is_relative_to("/mnt"):
        parser.error("Mounted paths are outside this audit's scope")
    # Store.events and Store.manifest need only a root. Avoid Store initialization's
    # index/database side effects when performing an offline journal audit.
    store = Store.__new__(Store)
    store.root = root
    result = {"schema_version": 2, "component_measure": "compact UTF-8 JSON bytes; nested components overlap; not billed tokens", "evaluation_eligible": False, "episodes": [audit(store, eid, args.record_exposure) for eid in args.episode_id]}
    for episode in result["episodes"]:
        episode["provider_continuation_diagnostics"] = continuation_diagnostics(
            store.events(episode["episode_id"])
        )
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    print(json.dumps({"output": str(output), "episodes": [{k: e[k] for k in ("episode_id", "request_count", "action_count", "usage", "recorded_cost_usd", "operation_counts", "helpers", "rejections", "final_public_resources")} for e in result["episodes"]]}))


if __name__ == "__main__":
    main()
