import importlib.util
import json
from copy import deepcopy

from balatro_horizons.config import ROOT

spec = importlib.util.spec_from_file_location(
    "audit_transcript_efficiency", ROOT / "scripts/audit_transcript_efficiency.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def transcript():
    turn = [
        {"type": "reasoning", "encrypted_content": "OPAQUE_SENTINEL"},
        {"type": "function_call", "call_id": "lookup", "name": "inspect_page", "arguments": "{}"},
    ]
    result = {"complete": True, "items": ["public information"]}
    base = [{"role": "user", "content": "current observation"}]

    def event(sequence, kind, decision, payload):
        return {"sequence": sequence, "type": kind, "observation_id": decision,
                "request_id": f"request-{sequence}", "payload": payload}

    def request(sequence, decision, messages):
        return event(sequence, "provider_request", decision, {"body": {
            "include": ["reasoning.encrypted_content"], "input": messages,
        }})

    events = [
        request(0, 0, base),
        event(1, "provider_response", None, {"body": {"output": turn}}),
        event(2, "helper_result", 0, {"result": result}),
        request(3, 0, base + deepcopy(turn) + [{
            "type": "function_call_output", "call_id": "lookup", "output": json.dumps(result),
        }]),
        request(4, 1, base),
    ]
    events[1]["request_id"] = events[0]["request_id"]
    return events


def test_exact_turn_and_tool_result_roundtrip_without_opaque_disclosure():
    result = module.continuation_diagnostics(transcript())
    assert result["helper_round_trips_checked"] == 1
    assert result["exact_turn_round_trips"] == 1
    assert result["encrypted_reasoning_items_preserved"] == 1
    assert result["matched_tool_results"] == 1
    assert result["decision_starts_checked"] == 2
    assert not result["failures"]
    assert "OPAQUE_SENTINEL" not in json.dumps(result)


def test_mutation_missing_result_and_across_action_leak_are_detected():
    events = transcript()
    events[3]["payload"]["body"]["input"][1]["encrypted_content"] = "mutated"
    events[3]["payload"]["body"]["input"][-1]["call_id"] = "wrong"
    events[4]["payload"]["body"]["input"] = deepcopy(events[3]["payload"]["body"]["input"])
    result = module.continuation_diagnostics(events)
    assert {failure["code"] for failure in result["failures"]} == {
        "DECISION_RESET_FAILED", "PROVIDER_TURN_CHANGED_OR_MISSING", "TOOL_RESULT_CHANGED_OR_MISSING",
    }
    assert result["exact_turn_round_trips"] == 0
    assert result["matched_tool_results"] == 0


def test_running_helper_does_not_claim_a_verified_roundtrip():
    result = module.continuation_diagnostics(transcript()[:3])
    assert result["helper_round_trips_checked"] == 0
    assert result["pending_helper_followups"] == 1
    assert not result["failures"]
