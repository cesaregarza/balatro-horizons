import json
from copy import deepcopy

import httpx
import pytest
from test_boundary import project
from test_provider_continuations import model

from balatro_horizons.config import Limits
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.context.build import decision_context
from balatro_horizons.harness.failures import HarnessFailure
from balatro_horizons.harness.input_limits import count_payload, request_size
from balatro_horizons.harness.money import reservation_usd
from balatro_horizons.harness.transport import DirectProvider


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_complete_helper_input_is_counted_unchanged_and_retry_reuses_count(provider, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    calls = []

    def receive(request):
        calls.append((request.url.path, json.loads(request.content)))
        if request.url.path.endswith(("/input_tokens", "/count_tokens")):
            return httpx.Response(200, json={"input_tokens": 6000})
        return httpx.Response(200, json={"status": "completed", "output": [], "content": []})

    limits = Limits()
    policy = DirectProvider(
        model(provider), limits, httpx.Client(transport=httpx.MockTransport(receive))
    )
    obs = project(FakeGame().observe_private())
    exchanges = []
    original_items = []
    for i in range(5):
        if provider == "openai":
            items = [
                {"type": "reasoning", "encrypted_content": "opaque" * 1400, "summary": []},
                {
                    "type": "function_call",
                    "call_id": f"call_{i}",
                    "name": "calculate",
                    "arguments": '{"expression":"1+1"}',
                },
            ]
        else:
            items = [
                {"type": "thinking", "thinking": "summary", "signature": "opaque" * 1400},
                {
                    "type": "tool_use",
                    "id": f"call_{i}",
                    "name": "calculate",
                    "input": {"expression": "1+1"},
                },
            ]
        original_items.extend(deepcopy(items))
        exchanges.append(
            {
                "operation": {"kind": "arithmetic", "expression": "1+1"},
                "result": {"result": "2"},
                "tool_call": {"name": "calculate", "arguments": {"expression": "1+1"}},
                "provider_turn": {
                    "version": "provider_turn_v1",
                    "provider": provider,
                    "items": items,
                },
            }
        )
    before = deepcopy(exchanges)
    ctx, delivered = decision_context(
        obs, exchanges,  byte_limit=limits.max_request_bytes
    )
    body = policy.request(ctx, delivered)
    assert request_size(body) > 32768
    measurement = policy.check_input(body)
    policy.send(body)
    policy.send(body)
    assert len(calls) == 3  # One count, two generation attempts.
    assert calls[0][1] == count_payload(body, provider)
    carried = (
        calls[0][1]["input"]
        if provider == "openai"
        else [
            item
            for msg in calls[0][1]["messages"]
            if msg["role"] == "assistant"
            for item in msg["content"]
        ]
    )
    comparable = [item for item in carried if item.get("type") in {"reasoning", "function_call"}]
    assert (comparable if provider == "openai" else carried) == original_items
    assert measurement["input_tokens"] == 6000
    assert exchanges == before
    assert len(delivered) == 5
    assert sum(e["result"].get("context_cleared") is True for e in delivered) == 2
    changed = deepcopy(body)
    changed["model"] += "-changed"
    policy.check_input(changed)
    assert len(calls) == 4


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"input_tokens": 40000}),
        httpx.Response(200, json={"input_tokens": True}),
        httpx.Response(200, json={"input_tokens": -1}),
        httpx.Response(200, text="not-json"),
        httpx.Response(400, json={"error": {"message": "PRIVATE_SENTINEL"}}),
    ],
)
def test_bad_or_excessive_count_never_generates(response, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    paths = []

    def receive(request):
        paths.append(request.url.path)
        return response

    policy = DirectProvider(
        model("openai"), Limits(), httpx.Client(transport=httpx.MockTransport(receive))
    )
    ctx, delivered = decision_context(
        project(FakeGame().observe_private()), []
    )
    with pytest.raises(HarnessFailure) as error:
        policy.send(policy.request(ctx, delivered))
    assert paths == ["/v1/responses/input_tokens"]
    assert "PRIVATE_SENTINEL" not in json.dumps(error.value.public())
    assert error.value.code in {"INPUT_TOKEN_LIMIT", "TOKEN_COUNT_UNAVAILABLE"}


def test_request_bytes_are_independent_of_token_and_cost_limits():
    model_config, limits = model("openai"), Limits()
    before = reservation_usd(model_config, limits)
    limits.max_request_bytes *= 2
    assert reservation_usd(model_config, limits) == before
    ctx, delivered = decision_context(
        project(FakeGame().observe_private()), []
    )
    limits.max_request_bytes = 1024
    policy = DirectProvider(model_config, limits)
    with pytest.raises(HarnessFailure, match="LOCAL_CONTEXT_LIMIT") as error:
        policy.request(ctx, delivered)
    assert error.value.details["request_bytes"] > error.value.details["byte_limit"]


def test_helper_overflow_has_safe_size_and_retention_diagnostics():
    obs = project(FakeGame().observe_private())
    initial, _ = decision_context(obs, [])
    exchange = {
        "operation": {"kind": "arithmetic", "expression": "1+1"},
        "result": {"result": "2"},
        "provider_turn": {
            "provider": "openai",
            "items": [{"type": "reasoning", "encrypted_content": "PRIVATE_SENTINEL" * 5000}],
        },
    }
    with pytest.raises(HarnessFailure, match="LOCAL_CONTEXT_LIMIT") as error:
        decision_context(
            obs,
            [exchange],
            byte_limit=initial["context_bytes_upper_bound"] + 100,
        )
    assert error.value.details["stage"] == "helper_followup"
    assert error.value.details["retained_provider_turns"] == 1
    assert "PRIVATE_SENTINEL" not in json.dumps(error.value.public())
