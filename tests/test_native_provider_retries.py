"""Runner-owned admission/retry scheduling and conservative settlement, all offline."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
from provider_transport import stream_response
from test_native_provider_streams import native_response
from test_provider_continuations import model

from balatro_horizons.config import Config, Limits
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.loop import Runner
from balatro_horizons.harness.money import Spending, reservation_usd
from balatro_horizons.harness.transport import DirectProvider, ProviderFailure
from balatro_horizons.harness.transport.errors import check_status, retry_after


@pytest.mark.parametrize("provider,status,code,retryable", [
    ("openai", 429, "rate_limit_exceeded", True),
    ("openai", 429, "slow_down", True),
    ("openai", 429, "insufficient_quota", False),
    ("openai", 429, "credit_balance_exhausted", False),
    ("openai", 429, "organization_spend_limit_exceeded", False),
    ("openai", 429, "project_spend_limit_exceeded", False),
    ("openai", 429, "organization_usage_limit_exceeded", False),
    ("openai", 503, "server_is_overloaded", True),
    ("openai", 401, "invalid_api_key", False),
    ("openai", 529, "server_error", False),
    ("anthropic", 529, "overloaded_error", True),
    ("anthropic", 429, "rate_limit_error", True),
    ("anthropic", 429, "enforced_spend_limit_reached", False),
    ("anthropic", 400, "invalid_request_error", False),
    ("anthropic", 401, "authentication_error", False),
    ("anthropic", 403, "permission_error", False),
    ("anthropic", 500, "api_error", True),
])
def test_native_http_taxonomy(provider, status, code, retryable):
    error = {"code": code, "message": "PRIVATE-ERROR-TEXT"} if provider == "openai" else {"type": code}
    if code == "enforced_spend_limit_reached":
        error = {"type": "rate_limit_error", "details": {"error_code": code}}
    response = httpx.Response(status, json={"error": error}, headers={"retry-after": "60"})
    with pytest.raises(ProviderFailure) as caught:
        check_status(response, provider)
    assert caught.value.provider_code == code
    assert caught.value.retryable is retryable
    assert caught.value.retry_after == 60
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("value,expected", [("2.5", 2.5), ("-1", 0), ("99999", 60),
                                             ("NaN", None), ("infinity", None), ("broken", None)])
def test_retry_after_is_bounded_and_finite(value, expected):
    assert retry_after({"retry-after": value}) == expected


def test_retry_after_http_date():
    date = format_datetime(datetime.now(UTC) + timedelta(seconds=20), usegmt=True)
    assert 18 <= retry_after({"retry-after": date}) <= 20


def abort_response(provider):
    result = native_response(provider)
    if provider == "openai":
        result["output"] = [{"type": "function_call", "call_id": "abort_1", "name": "abort_run",
                             "arguments": '{"reason":"mock complete"}', "status": "completed"}]
    else:
        result["content"] = [{"type": "tool_use", "id": "abort_1", "name": "abort_run",
                              "input": {"reason": "mock complete"}}]
    return result


def run_fixture(store, monkeypatch, provider, receive):
    limits = Limits(paid_calls_enabled=True, max_episode_cost_usd=5, max_batch_cost_usd=10)
    config = Config(budgets=limits)
    client = httpx.Client(transport=httpx.MockTransport(receive))
    policy = DirectProvider(model(provider), limits, client)
    monkeypatch.setenv(policy.key_name, "dummy-offline-key")
    spending = Spending.episode_only(store.root / "private_runs" / "test-native-spending.json", 5)
    runner = Runner(store, config, FakeGame(), policy, spending)
    return runner, policy, spending


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("stage", ["count", "generation"])
def test_retry_scheduler_is_bounded_stop_aware_and_counting_never_reserves(
    provider, stage, store, monkeypatch,
):
    counted, generated, waits = [], [], []

    def receive(request):
        body = json.loads(request.content)
        is_count = request.url.path.endswith(("/input_tokens", "/count_tokens"))
        (counted if is_count else generated).append(deepcopy(body))
        if is_count:
            assert runner.calls == 0
            assert runner.cost == 0
        retry_target = counted if stage == "count" else generated
        if is_count == (stage == "count") and len(retry_target) == 1:
            code = "rate_limit_exceeded" if provider == "openai" else "overloaded_error"
            return httpx.Response(429 if provider == "openai" else 529,
                                  json={"error": {"type": code}}, headers={"retry-after": "60"})
        if is_count:
            return httpx.Response(200, json={"input_tokens": 100})
        return stream_response(abort_response(provider), provider)

    runner, policy, spending = run_fixture(store, monkeypatch, provider, receive)
    monkeypatch.setattr(runner.stop, "wait", lambda delay: waits.append(delay) or False)
    result = runner.run()
    assert result["reason"] == "AGENT_ABORT"
    assert waits == [60]
    assert len(counted) == (2 if stage == "count" else 1)
    assert len(generated) == result["provider_calls"] == (1 if stage == "count" else 2)
    assert all(body == generated[0] for body in generated)
    entries = list(json.loads(spending.path.read_text()).values())
    assert len(entries) == len(generated)
    if stage == "generation":
        unsettled = [entry for entry in entries if not entry["settled"]]
        assert len(unsettled) == 1
        assert unsettled[0]["cost"] == reservation_usd(policy.model, policy.limits)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_missing_final_usage_never_executes_and_retains_unknown_reservation(provider, store, monkeypatch):
    def receive(request):
        if request.url.path.endswith(("/input_tokens", "/count_tokens")):
            return httpx.Response(200, json={"input_tokens": 100})
        response = abort_response(provider)
        response.pop("usage")
        return stream_response(response, provider)

    runner, policy, spending = run_fixture(store, monkeypatch, provider, receive)
    result = runner.run()
    assert result["reason"] == "PROVIDER_USAGE_UNKNOWN"
    assert result["provider_calls"] == 1 and result["committed_actions"] == 0
    (entry,) = json.loads(spending.path.read_text()).values()
    assert not entry["settled"] and entry["cost"] == reservation_usd(policy.model, policy.limits)
    assert policy.last_tool_call is None
    events = store.events(result["episode_id"])
    (response,) = [event["payload"] for event in events if event["type"] == "provider_response"]
    assert response["usage_known"] is False
    assert response["reserved_usd"] == entry["reserved"]
    assert "cost_usd" not in response


def test_stop_during_free_count_retry_has_no_generation_reservation(store, monkeypatch):
    def receive(_request):
        return httpx.Response(529, json={"error": {"type": "overloaded_error"}},
                              headers={"retry-after": "60"})

    runner, _, spending = run_fixture(store, monkeypatch, "anthropic", receive)
    monkeypatch.setattr(runner.stop, "wait", lambda _delay: True)
    result = runner.run()
    assert result["provider_calls"] == 0 and result["committed_actions"] == 0
    assert result["cost_usd"] == 0
    assert not spending.path.exists() or json.loads(spending.path.read_text()) == {}


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_stop_during_count_response_is_operator_abort_with_no_reservation(provider, store, monkeypatch):
    requests = []

    def receive(request):
        requests.append(request)
        runner.stop.set()
        return httpx.Response(200, json={"input_tokens": 100})

    runner, _, spending = run_fixture(store, monkeypatch, provider, receive)
    result = runner.run()
    assert result["outcome"] == "OPERATOR_ABORT"
    assert result["provider_calls"] == 0 and result["committed_actions"] == 0
    assert len(requests) == 1
    assert not spending.path.exists() or json.loads(spending.path.read_text()) == {}


def test_free_count_retries_stop_at_attempt_cap_without_generation(store, monkeypatch):
    requests, waits = [], []

    def receive(request):
        requests.append(request)
        return httpx.Response(529, json={"error": {"type": "overloaded_error"}})

    runner, _, spending = run_fixture(store, monkeypatch, "anthropic", receive)
    monkeypatch.setattr(runner.stop, "wait", lambda delay: waits.append(delay) or False)
    result = runner.run()
    assert result["reason"] == "TOKEN_COUNT_UNAVAILABLE"
    assert len(requests) == runner.limits.max_transport_attempts == 3
    assert waits == [1, 2]
    assert result["provider_calls"] == 0 and result["cost_usd"] == 0
    assert not spending.path.exists() or json.loads(spending.path.read_text()) == {}
