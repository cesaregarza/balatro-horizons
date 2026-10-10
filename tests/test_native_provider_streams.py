"""Real SSE shapes over mocked HTTP, including opaque continuation and lost reads."""

import json
from copy import deepcopy

import httpx
import pytest
from provider_transport import stream_response, with_input_count
from test_claude_support import context
from test_provider_continuations import model

from balatro_horizons.config import Limits
from balatro_horizons.harness.transport import DirectProvider, ProtocolFailure, ProviderFailure


def native_response(provider):
    call = {"name": "calculate", "type": "function_call", "id": "fc_1", "call_id": "call_1",
            "arguments": '{"expression":"1+1"}', "status": "completed"}
    usage = {"input_tokens": 20, "output_tokens": 12,
             "input_tokens_details": {"cached_tokens": 5, "cache_write_tokens": 2}}
    if provider == "openai":
        return {"id": "resp_1", "status": "completed", "usage": usage, "output": [
            {"id": "rs_1", "type": "reasoning", "encrypted_content": "opaque-final-state",
             "summary": [{"type": "summary_text", "text": "résumé"}]}, call,
        ]}
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-test",
            "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 20, "output_tokens": 12,
                      "cache_read_input_tokens": 5, "cache_creation_input_tokens": 0},
            "content": [
                {"type": "thinking", "thinking": "résumé", "signature": "opaque-signature"},
                {"type": "redacted_thinking", "data": "opaque-redacted"},
                {"type": "text", "text": "Check."},
                {"type": "tool_use", "id": "toolu_1", "name": "calculate",
                 "input": {"expression": "1+1"}},
            ]}


def stream_events(provider, final=None):
    final = final or native_response(provider)
    if provider == "openai":
        return [
            {"type": "response.created", "response": {"id": final["id"], "status": "in_progress", "output": []}},
            {"type": "response.output_item.added", "output_index": 0,
             "item": {"type": "reasoning", "id": "rs_1", "encrypted_content": "partial"}},
            {"type": "response.function_call_arguments.delta", "output_index": 1,
             "delta": '{"expression":"1+1"}'},
            *[{"type": "response.output_item.done", "output_index": index, "item": item}
              for index, item in enumerate(final["output"])],
            {"type": "response.completed", "response": final},
        ]
    initial = {**deepcopy(final), "content": [], "stop_reason": None,
               "usage": {**final["usage"], "output_tokens": 1}}
    events = [{"type": "message_start", "message": initial}, {"type": "ping"}]
    for index, block in enumerate(final["content"]):
        start, deltas = deepcopy(block), []
        if block["type"] == "thinking":
            start.update(thinking="", signature="")
            deltas = [{"type": "thinking_delta", "thinking": "résumé"},
                      {"type": "signature_delta", "signature": "opaque-"},
                      {"type": "signature_delta", "signature": "signature"}]
        elif block["type"] == "text":
            start["text"] = ""
            deltas = [{"type": "text_delta", "text": "Check."}]
        elif block["type"] == "tool_use":
            start["input"] = {}
            deltas = [{"type": "input_json_delta", "partial_json": '{"expression":'},
                      {"type": "input_json_delta", "partial_json": '"1+1"}'}]
        events.append({"type": "content_block_start", "index": index, "content_block": start})
        events.extend({"type": "content_block_delta", "index": index, "delta": delta} for delta in deltas)
        events.append({"type": "content_block_stop", "index": index})
    events.extend([
        {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
         "usage": {"output_tokens": 12}},
        {"type": "message_stop"},
    ])
    return events


class Fragments(httpx.SyncByteStream):
    def __init__(self, events, *, stride=7, before=None, fail=None):
        self.data = "".join(f"event: {event['type']}\r\ndata: {json.dumps(event, ensure_ascii=False)}\r\n\r\n"
                            for event in events).encode()
        self.stride, self.before, self.fail = stride, before, fail

    def __iter__(self):
        for index in range(0, len(self.data), self.stride):
            if self.before:
                self.before(index)
            yield self.data[index:index + self.stride]
        if self.fail:
            raise self.fail("private-disconnect")


def connected(provider, monkeypatch, events, **fragments):
    def receive(_request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              stream=Fragments(events, **fragments))

    client = httpx.Client(transport=httpx.MockTransport(with_input_count(receive)))
    policy = DirectProvider(model(provider), Limits(), client)
    monkeypatch.setenv(policy.key_name, "dummy-offline-key")
    return policy, policy.request(context(), [])


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("stride", [1, 7, 10000])
def test_fragmented_stream_assembles_exact_native_response_and_continuation(provider, stride, monkeypatch):
    final = native_response(provider)
    policy, body = connected(provider, monkeypatch, stream_events(provider, final), stride=stride)
    result = policy.send(body)
    assert result == final
    assert policy.last_tool_call is None and policy.last_provider_turn is None
    assert policy.parse(result) == {"kind": "arithmetic", "expression": "1+1"}
    items = final["output" if provider == "openai" else "content"]
    assert policy.last_provider_turn["items"] == items
    policy.on_decision_end()
    assert policy.last_provider_turn is None


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_full_tool_arguments_without_terminal_never_complete(provider, monkeypatch):
    policy, body = connected(provider, monkeypatch, stream_events(provider)[:-1])
    with pytest.raises(ProviderFailure, match="PROVIDER_STREAM_INCOMPLETE") as failure:
        policy.send(body)
    assert failure.value.retryable is False
    assert policy.last_tool_call is None and policy.last_provider_turn is None


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("failure", [httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError])
def test_partial_stream_disconnect_is_nonretryable(provider, failure, monkeypatch):
    policy, body = connected(provider, monkeypatch, stream_events(provider)[:-1], fail=failure)
    with pytest.raises(ProviderFailure) as caught:
        policy.send(body)
    assert not caught.value.retryable
    assert "private" not in str(caught.value)
    assert policy.last_tool_call is None


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_cancel_between_chunks_never_exposes_a_tool(provider, monkeypatch):
    stopped, consumed = [False], []

    def before(index):
        consumed.append(index)
        stopped[0] = index > 20

    policy, body = connected(provider, monkeypatch, stream_events(provider),
                             before=before)
    policy.bind_stop(lambda: stopped[0])
    with pytest.raises(ProviderFailure, match="PROVIDER_CANCELLED"):
        policy.send(body)
    assert policy.last_tool_call is None
    # A post-loop stop check is insufficient: abandon the stream at the next
    # chunk, before buffering the remaining response or reaching its terminal.
    assert consumed == [0, 7, 14, 21]


@pytest.mark.parametrize("provider,code", [("openai", "server_error"), ("anthropic", "overloaded_error")])
def test_stream_error_preserves_sanitized_diagnostic_without_blind_retry(provider, code, monkeypatch):
    events = stream_events(provider)[:-1] + [{"type": "error", "error": {
        "type": code, "message": "PRIVATE-UPSTREAM-PROMPT"}}]
    policy, body = connected(provider, monkeypatch, events)
    with pytest.raises(ProviderFailure, match="PROVIDER_STREAM_ERROR") as failure:
        policy.send(body)
    assert failure.value.provider_code == code
    assert not failure.value.retryable
    assert "PRIVATE" not in str(failure.value)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_json_generation_body_is_not_a_stream_fallback(provider, monkeypatch):
    requests = []

    def receive(request):
        requests.append(request)
        return httpx.Response(200, json={"input_tokens": 1} if "tokens" in request.url.path
                              else native_response(provider))

    policy = DirectProvider(model(provider), Limits(), httpx.Client(transport=httpx.MockTransport(receive)))
    monkeypatch.setenv(policy.key_name, "dummy-offline-key")
    with pytest.raises(ProviderFailure, match="PROVIDER_STREAM_REQUIRED"):
        policy.send(policy.request(context(), []))
    assert len(requests) == 2


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_missing_final_usage_remains_unknown(provider, monkeypatch):
    events = stream_events(provider)
    if provider == "openai":
        events[-1]["response"].pop("usage")
    else:
        events[-2].pop("usage")
    policy, body = connected(provider, monkeypatch, events)
    assembled = policy.send(body)
    assert policy.usage_cost(assembled, .7) == .7
    assert not policy.usage_known(assembled)


@pytest.mark.parametrize("provider,terminal,code", [
    ("openai", "incomplete", "PROVIDER_RESPONSE_INCOMPLETE"),
    ("openai", "failed", "PROVIDER_RESPONSE_FAILED"),
    ("anthropic", "max_tokens", "PROVIDER_RESPONSE_INCOMPLETE"),
    ("anthropic", "refusal", "PROVIDER_REFUSAL"),
])
def test_terminal_failure_cannot_execute_complete_looking_call(provider, terminal, code, monkeypatch):
    response = native_response(provider)
    response["status" if provider == "openai" else "stop_reason"] = terminal
    client = httpx.Client(transport=httpx.MockTransport(with_input_count(
        lambda _request: stream_response(response, provider))))
    policy = DirectProvider(model(provider), Limits(), client)
    monkeypatch.setenv(policy.key_name, "dummy-offline-key")
    completed = policy.send(policy.request(context(), []))
    assert policy.usage_known(completed)
    with pytest.raises(ProtocolFailure, match=code):
        policy.parse(completed)
    assert policy.last_tool_call is None


def test_openai_refusal_is_explicit_without_inventing_a_tool_call(monkeypatch):
    response = native_response("openai")
    response["output"] = [{"type": "message", "role": "assistant", "content": [
        {"type": "refusal", "refusal": "Cannot continue."}]}]
    policy, body = connected("openai", monkeypatch, stream_events("openai", response))
    with pytest.raises(ProtocolFailure, match="PROVIDER_REFUSAL"):
        policy.parse(policy.send(body))
    assert policy.last_tool_call is None
    assert policy.last_provider_turn["items"] == response["output"]


def test_empty_claude_response_feedback_uses_a_legal_user_message():
    policy = DirectProvider(model("anthropic"), Limits())
    ctx = context()
    policy.request(ctx, [])
    with pytest.raises(ProtocolFailure, match="NO_OPERATION"):
        policy.parse({"stop_reason": "end_turn", "content": []})
    body = policy.request(ctx, [{"provider_turn": policy.last_provider_turn,
                                 "model_result": {"result": {"error": "NO_OPERATION"},
                                                  "context_update": {}}}])
    assert all(message["role"] == "user" for message in body["messages"])
    assert "tool_use_id" not in json.dumps(body["messages"])
