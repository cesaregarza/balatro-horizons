"""Every terminal releases an already populated native decision continuation."""

import httpx
import pytest
from provider_transport import stream_response
from test_native_provider_retries import abort_response, run_fixture
from test_native_provider_streams import native_response


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_stop_before_first_generation_also_releases_reference_map(provider, store, monkeypatch):
    def count_only(request):
        assert request.url.path.endswith(("/input_tokens", "/count_tokens"))
        runner.stop.set()
        return httpx.Response(200, json={"input_tokens": 100})

    runner, policy, _spending = run_fixture(store, monkeypatch, provider, count_only)
    try:
        result = runner.run()
        assert result["reason"] == "OPERATOR_REQUEST"
        assert result["provider_calls"] == 0
        assert policy.last_tool_call is policy.last_provider_turn is policy.model_references is None
    finally:
        policy.client.close()


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize(("terminal", "reason"), [
    ("abort", "AGENT_ABORT"),
    ("disconnect", "PROVIDER_RESPONSE_LOST"),
    ("stop", "OPERATOR_REQUEST"),
    ("invalid", "AGENT_PROTOCOL_FAILURE"),
])
def test_terminal_clears_previous_helper_continuation(provider, terminal, reason, store, monkeypatch):
    generated = []

    def receive(request):
        if request.url.path.endswith(("/input_tokens", "/count_tokens")):
            return httpx.Response(200, json={"input_tokens": 100})
        generated.append(request)
        if len(generated) == 1:
            return stream_response(native_response(provider), provider)
        assert policy.last_provider_turn is not None  # A real helper turn preceded the terminal.
        if terminal == "disconnect":
            raise httpx.ReadError("private transport detail")
        if terminal == "stop":
            runner.stop.set()
        response = abort_response(provider)
        if terminal == "invalid":
            call = response["output" if provider == "openai" else "content"][0]
            call["name"] = "calculate"
            call["arguments" if provider == "openai" else "input"] = (
                '{"expression":12}' if provider == "openai" else {"expression": 12}
            )
        return stream_response(response, provider)

    runner, policy, _spending = run_fixture(store, monkeypatch, provider, receive)
    runner.limits.max_consecutive_invalid_actions = 1
    try:
        result = runner.run()
        assert result["reason"] == reason
        assert len(generated) == result["provider_calls"] == 2
        assert result["committed_actions"] == 0
        assert policy.last_tool_call is policy.last_provider_turn is policy.model_references is None
        assert len([event for event in store.events(result["episode_id"])
                    if event["type"] == "helper_result"]) == 1
    finally:
        policy.client.close()
