import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from balatro_horizons.api import create_app
from balatro_horizons.review.dev_trace import decision_trace
from balatro_horizons.review.service import ReviewError, ReviewService


@pytest.fixture
def recording(store, episode):
    manifest = store.manifest(episode)
    eid = store.create({"evidence_kind": "SYNTHETIC_TEST", "agent": "heuristic",
                        "config": manifest["config"]}, {"seed": "PRIVATE_DEV_TRACE_SEED"})
    observation = deepcopy(next(e["payload"] for e in store.events(episode) if e["type"] == "observation"))
    observation.update(episode_id=eid, observation_id=67)
    store.append(eid, "observation", observation, observation_id=67)
    review = ReviewService(store)
    token = review.open(eid, retrospective=True)["review_token"]
    return eid, review, token, observation


def request(store, eid, rid, body=None, attempt=1):
    return store.append(eid, "provider_request", {"body": body or {"tools": [], "input": []},
                        "reserved_usd": 0.02, "attempt": attempt}, request_id=rid, observation_id=67)


def response(store, eid, rid, body):
    return store.append(eid, "provider_response", {"body": body, "cost_usd": 0.001}, request_id=rid)


def test_openai_exact_calls_results_context_and_pending_request(recording, store):
    eid, review, token, _ = recording
    store.append(eid, "agent_context", {"context": {"current_costs": {"reroll": "5"},
                 "run_notebook": {"plan": "Save cash"}}}, observation_id=67)
    request(store, eid, "first")
    response(store, eid, "first", {"output": [
        {"type": "reasoning", "encrypted_content": "opaque secret", "summary": [{"text": "Returned summary"}]},
        {"type": "function_call", "call_id": "tool-one", "name": "inspect_page", "arguments": '{"page":"hand_levels"}'},
    ], "usage": {"input_tokens": 1234}})
    helper = store.append(eid, "helper_result", {"operation": {"kind": "inspect"},
                           "result": {"hand_levels": {"Pair": "L2"}}}, observation_id=67)
    request(store, eid, "second", {"input": [
        {"type": "function_call_output", "call_id": "tool-one", "output": '{"hand_levels":{"Pair":"L2"}}'}]})
    before = (store.episode_path(eid)/"events.jsonl").read_bytes()
    trace = decision_trace(review, token, 67)
    assert not trace["complete"] and len(trace["calls"]) == 2
    first, second = trace["calls"]
    assert first["tools"][0]["call_id"] == "tool-one"
    assert first["tools"][0]["arguments"] == {"page": "hand_levels"}
    assert first["tools"][0]["delivered_results"][0]["content"] == {"hand_levels": {"Pair": "L2"}}
    assert helper["event_id"] in [e["event_id"] for e in first["journal_events"]]
    assert second["status"] == "waiting_for_response"
    assert first["context_event_id"] and "Save cash" in json.dumps(trace)
    assert "opaque secret" not in json.dumps(trace)
    assert "opaque_continuation_omitted" in json.dumps(trace)
    assert (store.episode_path(eid)/"events.jsonl").read_bytes() == before
    assert review.exposure(eid)["records"][-1]["kind"] == "dev_trace"


def test_anthropic_call_ids_and_opaque_material(recording, store):
    eid, review, token, _ = recording
    request(store, eid, "first")
    response(store, eid, "first", {"content": [
        {"type": "thinking", "thinking": "Provider returned text", "signature": "secret-signature"},
        {"type": "redacted_thinking", "data": "secret-redacted"},
        {"type": "tool_use", "id": "toolu_1", "name": "calculate", "input": {"expression": "2+2"}},
    ]})
    request(store, eid, "second", {"messages": [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": '{"value":4}'}]}]})
    trace = decision_trace(review, token, 67)
    tool = trace["calls"][0]["tools"][0]
    assert tool["arguments"] == {"expression": "2+2"}
    assert tool["delivered_results"][0]["content"] == {"value": 4}
    assert "secret-signature" not in json.dumps(trace) and "secret-redacted" not in json.dumps(trace)


def test_retries_invalid_multiple_calls_and_future_decision_isolation(recording, store):
    eid, review, token, observation = recording
    request(store, eid, "failed")
    store.append(eid, "provider_error", {"code": "TIMEOUT", "usage": "unknown"}, request_id="failed")
    request(store, eid, "retry", attempt=2)
    response(store, eid, "retry", {"output": [
        {"type": "function_call", "call_id": "a", "name": "reroll_shop", "arguments": "{invalid"},
        {"type": "function_call", "call_id": "b", "name": "leave_shop", "arguments": "{}"},
    ]})
    store.append(eid, "action_rejected", {"code": "MULTIPLE_OPERATIONS"}, observation_id=67)
    store.append(eid, "raw_engine", {"private": "not public"})
    next_observation = {**observation, "observation_id": 68}
    store.append(eid, "observation", next_observation, observation_id=68)
    store.append(eid, "agent_context", {"future": "LATER_DECISION_ONLY"}, observation_id=68)
    trace = decision_trace(review, token, 67)
    first, retry = trace["calls"]
    assert first["status"] == "transport_error" and first["error_event"]["payload"]["code"] == "TIMEOUT"
    assert retry["status"] == "action_rejected" and len(retry["tools"]) == 2
    assert retry["tools"][0]["arguments_parse_error"]
    assert retry["tools"][0]["raw_arguments"] == "{invalid"
    assert trace["complete"] and trace["transition"]["observation_id"] == 68
    assert "LATER_DECISION_ONLY" not in json.dumps(trace) and "not public" not in json.dumps(trace)


def test_trace_requires_retrospective_session_even_after_exposure(recording, store, config):
    eid, review, token, _ = recording
    prospective = review.open(eid)["review_token"]
    with pytest.raises(ReviewError, match="RETROSPECTIVE_REVIEW_REQUIRED"):
        decision_trace(review, prospective, 67)
    with TestClient(create_app(store.root, config)) as client:
        path = "/api/review/decisions/67/trace"
        assert client.get(path).status_code == 403
        denied = client.get(path, headers={"X-Review-Token": prospective})
        assert denied.status_code == 400 and denied.json()["error"] == "RETROSPECTIVE_REVIEW_REQUIRED"
        assert client.get(path, headers={"X-Review-Token": token}).status_code == 200
        assert client.get("/api/review/decisions/999/trace", headers={"X-Review-Token": token}).status_code == 400


@pytest.mark.parametrize("text", ["PRIVATE_DEV_TRACE_SEED", "sk-proj-abcdefghijklmnopqrstuvwxyz"])
def test_sensitive_text_fails_closed(recording, store, text):
    eid, review, token, _ = recording
    request(store, eid, "leak", {"input": [{"role": "user", "content": text}]})
    with pytest.raises(ValueError, match="EXPORT_PRIVACY_SCAN_FAILED"):
        decision_trace(review, token, 67)


def test_helper_only_navigation_is_separate_from_game_actions(recording, store):
    eid, review, token, _ = recording
    assert review.decisions(token)["pending_decisions"] == []
    store.append(eid, "agent_context", {"context": "not included in ledger"}, observation_id=67)
    request(store, eid, "waiting")
    ledger = review.decisions(token)
    assert not ledger["actions"] and not ledger["uncommitted_actions"]
    assert ledger["pending_decisions"][0]["decision"] == 67
    assert ledger["pending_decisions"][0]["status"] == "awaiting_model"
    assert "not included in ledger" not in json.dumps(ledger)


def test_missing_response_is_not_claimed_pending_after_terminal(recording, store):
    eid, review, token, _ = recording
    request(store, eid, "lost")
    store.append(eid, "terminal", {"outcome": "INFRASTRUCTURE_FAILURE"})
    trace = decision_trace(review, token, 67)
    assert trace["complete"] and trace["calls"][0]["status"] == "no_recorded_response"


@pytest.mark.parametrize("body", [{"output": None}, {"output": "not blocks"}, None])
def test_malformed_provider_body_stays_inspectable(recording, store, body):
    eid, review, token, _ = recording
    request(store, eid, "malformed")
    response(store, eid, "malformed", body)
    trace = decision_trace(review, token, 67)
    assert trace["calls"][0]["response_event"]["payload"]["body"] == body
    assert trace["calls"][0]["tools"] == []
