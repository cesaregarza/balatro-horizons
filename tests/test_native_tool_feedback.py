"""Meter complete responses before bounded, provider-native correction rounds."""

import json

import httpx
import pytest
from provider_transport import stream_response, with_input_count
from test_native_provider_retries import abort_response, run_fixture
from test_native_provider_streams import Fragments, native_response, stream_events

from balatro_horizons.harness.money import reservation_usd


def argument_events(provider, raw):
    final = native_response(provider)
    call = final["output" if provider == "openai" else "content"][-1]
    call["name"] = "select_blind"
    if provider == "openai":
        call["arguments"] = raw
    events = stream_events(provider, final)
    first = True
    for event in events:
        delta = event.get("delta")
        if isinstance(delta, dict) and delta.get("type") == "input_json_delta":
            delta["partial_json"] = raw if first else ""
            first = False
    return events


def response(events):
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          stream=Fragments(events, stride=101))


def feedback(body, provider):
    if provider == "openai":
        output = body["input"][-1]
        assert output["type"] == "function_call_output" and output["call_id"] == "call_1"
        return json.loads(output["output"])["result"]
    output = body["messages"][-1]["content"][0]
    assert output["type"] == "tool_result" and output["tool_use_id"] == "toolu_1"
    assert output["is_error"] is True
    return json.loads(output["content"])["result"]


def corrective_round(store, monkeypatch, provider, raw):
    requests = []

    def receive(request):
        requests.append(json.loads(request.content))
        return (response(argument_events(provider, raw)) if len(requests) == 1
                else stream_response(abort_response(provider), provider))

    runner, policy, spending = run_fixture(store, monkeypatch, provider, with_input_count(receive))
    result = runner.run()
    assert result["reason"] == "AGENT_ABORT"
    assert result["committed_actions"] == 0 and result["provider_calls"] == len(requests) == 2
    entries = list(json.loads(spending.path.read_text()).values())
    assert len(entries) == 2 and all(entry["settled"] for entry in entries)
    field = "input" if provider == "openai" else "messages"
    assert requests[1][field][:len(requests[0][field])] == requests[0][field]
    assert policy.last_provider_turn is None  # Terminal cleanup still runs.
    return requests[1], feedback(requests[1], provider)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("raw,code", [
    ('{"observation_id":', "INVALID_OPERATION_JSON"),
    ('{"observation_id":0,"observation_id":1}', "INVALID_OPERATION_JSON"),
    ('{"blind_id":NaN}', "INVALID_OPERATION_JSON"),
    ('[]', "TOOL_ARGUMENTS_MUST_BE_OBJECT"),
    ('null', "TOOL_ARGUMENTS_MUST_BE_OBJECT"),
    ('"not an object"', "TOOL_ARGUMENTS_MUST_BE_OBJECT"),
])
def test_bad_tool_json_is_metered_then_gets_native_feedback_without_execution(
    provider, raw, code, store, monkeypatch,
):
    body, error = corrective_round(store, monkeypatch, provider, raw)
    assert error["error"] == code and error["game_advanced"] is False
    if provider == "openai":
        assert body["input"][-2]["arguments"] == raw
        assert body["input"][-3]["encrypted_content"] == "opaque-final-state"
    else:
        blocks = body["messages"][-2]["content"]
        assert blocks[:-1] == native_response(provider)["content"][:-1]
        assert blocks[-1] == {"type": "tool_use", "id": "toolu_1", "name": "select_blind",
                              "input": {"INVALID_JSON": raw}}
        assert "_harness_tool_input_error" not in json.dumps(body)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("extras_only", [False, True])
def test_missing_nullable_and_unexpected_keys_reach_model_feedback(provider, extras_only, store, monkeypatch):
    args = {"observation_id": 0, "blind_id": 1, "unexpected": "unneeded value"}
    if extras_only:
        args.update(note_update=None, decision_note=None)
    _, error = corrective_round(store, monkeypatch, provider, json.dumps(args))
    assert error["error"] == "INVALID_TOOL_ARGUMENTS"
    assert error["argument_path"] == "$"
    assert error["missing_keys"] == ([] if extras_only else ["decision_note", "note_update"])
    assert error["unexpected_keys"] == ["unexpected"]
    assert "null" in error["message"] and "unneeded value" not in json.dumps(error)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_invalid_tool_json_stops_at_shared_invalid_allowance(provider, store, monkeypatch):
    requests = []

    def receive(request):
        requests.append(json.loads(request.content))
        return response(argument_events(provider, "{"))

    runner, _, spending = run_fixture(store, monkeypatch, provider, with_input_count(receive))
    result = runner.run()
    assert result["outcome"] == result["reason"] == "AGENT_PROTOCOL_FAILURE"
    assert result["provider_calls"] == len(requests) == runner.limits.max_consecutive_invalid_actions
    assert result["committed_actions"] == 0
    entries = list(json.loads(spending.path.read_text()).values())
    assert len(entries) == len(requests) and all(entry["settled"] for entry in entries)
    field = "input" if provider == "openai" else "messages"
    for old, new in zip(requests, requests[1:], strict=False):
        assert new[field][:len(old[field])] == old[field]
        assert feedback(new, provider)["error"] == "INVALID_OPERATION_JSON"


@pytest.mark.parametrize("fault,code", [
    ("missing_usage", "PROVIDER_USAGE_UNKNOWN"),
    ("missing_terminal", "PROVIDER_STREAM_INCOMPLETE"),
    ("bad_delta", "PROVIDER_STREAM_INVALID"),
    ("overwritten_parse_error", "PROVIDER_STREAM_INVALID"),
    ("malformed_sse", "PROVIDER_STREAM_INVALID"),
    ("duplicate_sse_key", "PROVIDER_STREAM_INVALID"),
])
def test_invalid_tool_input_does_not_weaken_stream_or_usage_guards(fault, code, store, monkeypatch):
    generated = []

    def receive(request):
        generated.append(request)
        events = argument_events("anthropic", "{")
        if fault == "missing_usage":
            events[-2].pop("usage")
        elif fault == "missing_terminal":
            events.pop()
        elif fault == "bad_delta":
            next(event["delta"] for event in events if isinstance(event.get("delta"), dict)
                 and event["delta"].get("type") == "input_json_delta")["partial_json"] = None
        elif fault == "overwritten_parse_error":
            events[-2]["delta"]["_harness_tool_input_error"] = None
        elif fault in ("malformed_sse", "duplicate_sse_key"):
            data = "{" if fault == "malformed_sse" else '{"type":"ping","type":"ping"}'
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=f"data: {data}\n\n")
        return response(events)

    runner, policy, spending = run_fixture(store, monkeypatch, "anthropic", with_input_count(receive))
    result = runner.run()
    assert result["reason"] == code and result["outcome"] == "INFRASTRUCTURE_FAILURE"
    assert result["provider_calls"] == len(generated) == 1 and result["committed_actions"] == 0
    (entry,) = json.loads(spending.path.read_text()).values()
    assert not entry["settled"] and entry["cost"] == reservation_usd(policy.model, policy.limits)
    assert not any(event["type"] == "action_rejected" for event in store.events(result["episode_id"]))
