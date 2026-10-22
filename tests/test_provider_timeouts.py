"""Response-phase failures stop retries; all HTTP and gameplay are synthetic."""

import json

import httpx
import pytest
from provider_transport import with_input_count
from test_boundary import project
from test_providers_evaluation import model

from balatro_horizons.config import Config, Limits
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.context.build import context
from balatro_horizons.harness.loop import Runner
from balatro_horizons.harness.money import Spending, reservation_usd
from balatro_horizons.harness.transport import DirectProvider, ProviderFailure


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize(
    "failure,code,retryable",
    [
        (httpx.ReadTimeout, "PROVIDER_READ_TIMEOUT", False),
        (httpx.ConnectTimeout, "PROVIDER_TRANSPORT_UNKNOWN", True),
        (httpx.PoolTimeout, "PROVIDER_TRANSPORT_UNKNOWN", True),
        (httpx.WriteTimeout, "PROVIDER_TRANSPORT_UNKNOWN", False),
        (httpx.ConnectError, "PROVIDER_TRANSPORT_UNKNOWN", True),
        (httpx.WriteError, "PROVIDER_TRANSPORT_UNKNOWN", False),
        (httpx.ReadError, "PROVIDER_RESPONSE_LOST", False),
        (httpx.RemoteProtocolError, "PROVIDER_RESPONSE_LOST", False),
        (httpx.TransportError, "PROVIDER_TRANSPORT_UNKNOWN", False),
    ],
)
def test_transport_error_classification(provider, failure, code, retryable, monkeypatch):
    def receive(request):
        raise failure("PRIVATE_TIMEOUT_DETAIL", request=request)

    with httpx.Client(transport=httpx.MockTransport(with_input_count(receive))) as client:
        policy = DirectProvider(model(provider), Limits(), client=client)
        monkeypatch.setenv(policy.key_name, "mock-only")
        body = policy.request(context(project(FakeGame().observe_private())), [])
        with pytest.raises(ProviderFailure) as caught:
            policy.send(body)
    assert caught.value.code == str(caught.value) == code
    assert caught.value.retryable is retryable
    assert caught.value.provider_code is None


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("output_limit", [8192, 32_768])
@pytest.mark.parametrize(
    "failure,code",
    [
        (httpx.ReadTimeout, "PROVIDER_READ_TIMEOUT"),
        (httpx.ReadError, "PROVIDER_RESPONSE_LOST"),
        (httpx.RemoteProtocolError, "PROVIDER_RESPONSE_LOST"),
        (httpx.WriteError, "PROVIDER_TRANSPORT_UNKNOWN"),
        (httpx.WriteTimeout, "PROVIDER_TRANSPORT_UNKNOWN"),
    ],
)
def test_response_failure_never_retries_and_retains_one_reservation(
    provider, output_limit, failure, code, store, monkeypatch
):
    config = Config(budgets=Limits(
        max_output_tokens_per_call=output_limit,
        paid_calls_enabled=True, max_episode_cost_usd=5, max_batch_cost_usd=10,
    ))
    requests = []

    def receive(request):
        requests.append(request)
        raise failure("PRIVATE_TIMEOUT_DETAIL", request=request)

    spending = Spending(store.root / "private_runs" / "timeout-spending.json", 10)
    with httpx.Client(transport=httpx.MockTransport(with_input_count(receive))) as client:
        policy = DirectProvider(model(provider), config.budgets, client=client)
        monkeypatch.setenv(policy.key_name, "mock-only")
        reserve = reservation_usd(policy.model, config.budgets)
        assert config.budgets.max_transport_attempts == 3
        assert reserve * 3 < config.budgets.max_episode_cost_usd
        result = Runner(store, config, FakeGame(), policy, spending).run()

    assert len(requests) == result["provider_calls"] == 1
    assert result["outcome"] == "INFRASTRUCTURE_FAILURE"
    assert result["reason"] == code
    assert result["committed_actions"] == 0
    assert result["cost_usd"] == pytest.approx(reserve)
    entries = json.loads(spending.path.read_text())
    (entry,) = entries.values()
    assert entry == {
        "episode_id": result["episode_id"], "cost": reserve,
        "reserved": reserve, "settled": False,
    }
    events = store.events(result["episode_id"])
    (reservation,) = [event for event in events if event["type"] == "provider_reservation"]
    assert reservation["request_id"] in entries
    assert reservation["payload"] == {"attempt": 1, "reserved_usd": reserve}
    assert [event["payload"] for event in events if event["type"] == "provider_error"] == [{
        "code": code, "provider_code": None, "usage": "unknown",
    }]
    assert not any(event["type"] == "provider_response" for event in events)
    assert "PRIVATE_TIMEOUT_DETAIL" not in json.dumps(events)
