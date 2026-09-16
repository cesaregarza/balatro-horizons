"""Contract tests use mocked HTTP and the explicitly synthetic game only."""

import json

import httpx
import pytest
from pydantic import ValidationError
from test_boundary import project

from balatro_horizons.agents.baselines import Baseline
from balatro_horizons.agents.protocol import context
from balatro_horizons.agents.providers import DirectProvider, ProtocolFailure, ProviderFailure
from balatro_horizons.config import ROOT, Limits, ModelConfig, load_config
from balatro_horizons.engine.fake import FakeGame
from balatro_horizons.review.service import ReviewService
from balatro_horizons.runner import Runner


def luna():
    return load_config(ROOT / "configs/luna-smoke.yaml")


def response(operation):
    return {
        "model": "gpt-5.6-luna",
        "status": "completed",
        "service_tier": "default",
        "output": [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "MOCK_SUMMARY"}]},
            {
                "type": "function_call",
                "name": "operate",
                "status": "completed",
                "arguments": json.dumps(operation),
            },
        ],
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 200,
            "output_tokens_details": {"reasoning_tokens": 150},
        },
    }


def test_luna_request_is_explicit_stateless_standard_and_summary_opted_in():
    config = luna()
    policy = DirectProvider(config.models["luna"], config.budgets)
    body = policy.request(context(project(FakeGame().observe_private())), [])
    assert body["model"] == "gpt-5.6-luna"
    assert body["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert body["service_tier"] == "default"
    assert body["prompt_cache_options"] == {"mode": "explicit"}
    assert body["truncation"] == "disabled" and body["store"] is False
    assert len(body["tools"]) == 1 and body["tools"][0]["name"] == "operate"
    assert "previous_response_id" not in body and "temperature" not in body
    assert not config.budgets.paid_calls_enabled
    assert (config.budgets.max_episode_cost_usd, config.budgets.max_batch_cost_usd) == (1, 5)
    # Reasoning tokens are included in output_tokens, not added again.
    assert policy.usage_cost(response({}), 1) == pytest.approx(0.00044)


@pytest.mark.parametrize(
    "settings",
    [
        {"reasoning_effort": "minimal"},
        {"reasoning_summary": "raw"},
        {"reasoning_summary": None},
    ],
)
def test_luna_invalid_settings_rejected(settings):
    model = luna().models["luna"].model_dump()
    with pytest.raises(ValidationError):
        ModelConfig.model_validate({**model, "settings": settings})


def test_max_effort_supported_and_long_context_needs_separate_pricing():
    model = luna().models["luna"].model_dump()
    model = ModelConfig.model_validate({**model, "settings": {"reasoning_effort": "max"}})
    policy = DirectProvider(model, Limits(max_input_tokens_per_call=272001))
    with pytest.raises(ProviderFailure, match="LONG_CONTEXT_PRICING"):
        policy.request(context(project(FakeGame().observe_private())), [])


@pytest.mark.parametrize("status", ["incomplete", "failed", "cancelled", "in_progress"])
def test_unfinished_response_cannot_execute_a_function(status):
    config = luna()
    policy = DirectProvider(config.models["luna"], config.budgets)
    body = response({"kind": "abort", "reason": "test"})
    body["status"] = status
    with pytest.raises(ProtocolFailure):
        policy.parse(body)


@pytest.mark.parametrize(
    "usage",
    [
        None,
        [],
        {"input_tokens": -1, "output_tokens": 0},
        {"input_tokens": True, "output_tokens": 0},
        {"input_tokens": 1, "output_tokens": -1},
    ],
)
def test_unusable_usage_keeps_reservation(usage):
    config = luna()
    policy = DirectProvider(config.models["luna"], config.budgets)
    assert policy.usage_cost({"usage": usage}, 0.25) == 0.25


def test_mock_luna_full_runner_helpers_memory_summary_and_prospective_review(store, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "mock-only-not-a-real-credential")
    config = luna()
    config.budgets.paid_calls_enabled = True
    received = []
    baseline = Baseline("heuristic")

    def receive(request):
        body = json.loads(request.content)
        received.append(body)
        ctx = json.loads(body["input"][0]["content"])
        if len(received) == 1:
            op = {"kind": "arithmetic", "expression": "2+2"}
        else:
            op = baseline.decide(ctx, [])
            if op["kind"] == "action":
                op["envelope"]["memory_update"] = "MOCK_MEMORY"
                op["envelope"]["decision_note"] = "MOCK_NOTE"
        return httpx.Response(200, json=response(op))

    policy = DirectProvider(
        config.models["luna"],
        config.budgets,
        client=httpx.Client(transport=httpx.MockTransport(receive)),
    )
    summary = Runner(store, config, FakeGame(), policy).run()
    assert summary["outcome"] == "WIN" and summary["evidence_kind"] == "SYNTHETIC_TEST"
    assert summary["provider_calls"] > summary["committed_actions"] > 0
    assert summary["cost_usd"] == pytest.approx(len(received) * 0.00044)
    assert json.loads(received[1]["input"][-1]["content"])["operation_result"]["result"] == "4"
    assert "MOCK_MEMORY" in received[2]["input"][0]["content"]
    assert "MOCK_SUMMARY" not in json.dumps(received)  # summaries are not implicit memory
    events = store.events(summary["episode_id"])
    logged = [e for e in events if e["type"] == "provider_response"]
    assert len(logged) == len(received)
    assert logged[0]["payload"]["body"] == response({"kind": "arithmetic", "expression": "2+2"})
    assert "mock-only-not-a-real-credential" not in json.dumps(events)
    review = ReviewService(store)
    session = review.open(summary["episode_id"])
    assert "MOCK_SUMMARY" not in json.dumps(session["view"])
    assert "MOCK_SUMMARY" in json.dumps(review.advance(session["review_token"]))


def test_unknown_http_attempts_consume_budget_before_retry(store, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "mock-only")
    config = luna()
    config.budgets.paid_calls_enabled = True
    config.budgets.max_episode_cost_usd = 0.02  # one 0.016384 reservation fits
    calls = []

    def receive(request):
        calls.append(request)
        raise httpx.ReadTimeout("mock timeout")

    policy = DirectProvider(
        config.models["luna"],
        config.budgets,
        client=httpx.Client(transport=httpx.MockTransport(receive)),
    )
    summary = Runner(store, config, FakeGame(), policy).run()
    assert summary["outcome"] == "BUDGET_EXHAUSTED"
    assert len(calls) == 1 and summary["committed_actions"] == 0
    assert summary["cost_usd"] == pytest.approx(0.016384)


@pytest.mark.parametrize("quota_code", ["insufficient_quota", "credit_balance_exhausted"])
def test_quota_failure_is_not_retried_and_private_error_text_is_not_logged(
    store, monkeypatch, quota_code
):
    monkeypatch.setenv("OPENAI_API_KEY", "mock-only")
    config = luna()
    config.budgets.paid_calls_enabled = True
    requests = []

    def receive(request):
        requests.append(request)
        return httpx.Response(
            429,
            json={
                "error": {
                    "code": quota_code,
                    "message": "PRIVATE_UPSTREAM_TEXT",
                }
            },
        )

    policy = DirectProvider(
        config.models["luna"],
        config.budgets,
        client=httpx.Client(transport=httpx.MockTransport(receive)),
    )
    result = Runner(store, config, FakeGame(), policy).run()
    events = store.events(result["episode_id"])
    assert len(requests) == result["provider_calls"] == 1
    assert result["outcome"] == "INFRASTRUCTURE_FAILURE"
    errors = [e["payload"] for e in events if e["type"] == "provider_error"]
    assert errors[0]["provider_code"] == quota_code
    assert "PRIVATE_UPSTREAM_TEXT" not in json.dumps(events)
    review = ReviewService(store)
    opened = review.open(result["episode_id"])
    assert quota_code not in json.dumps(opened["view"])
    assert quota_code in json.dumps(review.advance(opened["review_token"]))


def test_prompt_cache_comparison_uses_only_last_explicitly_completed_response(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "mock-only")
    replies = iter(
        [
            {"id": "resp_completed_1", "status": "completed"},
            {"id": "resp_incomplete", "status": "incomplete"},
            {"id": "resp_missing_status"},
            {"id": "", "status": "completed"},
            {"id": "resp_completed_2", "status": "completed"},
        ]
    )

    def receive(_request):
        return httpx.Response(200, json=next(replies))

    config = luna()
    policy = DirectProvider(
        config.models["luna"],
        config.budgets,
        client=httpx.Client(transport=httpx.MockTransport(receive)),
    )
    ctx = context(project(FakeGame().observe_private()))

    first = policy.request(ctx, [])
    assert first["prompt_cache_options"] == {"mode": "explicit"}
    policy.send(first)

    second = policy.request(ctx, [])
    assert second["prompt_cache_options"] == {
        "mode": "explicit",
        "comparison_response_id": "resp_completed_1",
    }
    assert "previous_response_id" not in second

    for _ in range(3):
        policy.send(second)
        assert policy.request(ctx, [])["prompt_cache_options"]["comparison_response_id"] == (
            "resp_completed_1"
        )

    policy.send(second)
    assert policy.request(ctx, [])["prompt_cache_options"]["comparison_response_id"] == (
        "resp_completed_2"
    )


@pytest.mark.parametrize(
    "diagnostics",
    [
        None,
        {"type": "comparison_response_not_found"},
        {"type": "unavailable"},
        {
            "type": "cache_miss",
            "reason": "tools_changed",
            "comparison_reusable_tokens": 5629,
            "cache_missed_tokens": 5629,
        },
    ],
)
def test_prompt_cache_diagnostics_are_optional_metadata(diagnostics):
    config = luna()
    policy = DirectProvider(config.models["luna"], config.budgets)
    payload = response({"kind": "abort", "reason": "test"})
    if diagnostics is not None:
        payload["prompt_cache_diagnostics"] = diagnostics
    assert policy.parse(payload) == {"kind": "abort", "reason": "test"}


def test_prompt_cache_comparison_is_episode_local_and_supported_models_only(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "mock-only")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "mock-only")

    def completed(_request):
        return httpx.Response(200, json={"id": "resp_episode_1", "status": "completed"})

    config = luna()
    ctx = context(project(FakeGame().observe_private()))
    terra_model = ModelConfig.model_validate(
        {**config.models["luna"].model_dump(), "model": "gpt-5.6-terra"}
    )
    terra = DirectProvider(
        terra_model,
        config.budgets,
        client=httpx.Client(transport=httpx.MockTransport(completed)),
    )
    first = terra.request(ctx, [])
    assert "prompt_cache_options" not in first
    terra.send(first)
    assert terra.request(ctx, [])["prompt_cache_options"] == {
        "comparison_response_id": "resp_episode_1"
    }

    fresh_episode = DirectProvider(terra_model, config.budgets)
    assert "prompt_cache_options" not in fresh_episode.request(ctx, [])

    unsupported_model = ModelConfig.model_validate(
        {**config.models["luna"].model_dump(), "model": "gpt-5.5"}
    )
    unsupported = DirectProvider(
        unsupported_model,
        config.budgets,
        client=httpx.Client(transport=httpx.MockTransport(completed)),
    )
    unsupported.send(unsupported.request(ctx, []))
    assert "prompt_cache_options" not in unsupported.request(ctx, [])

    anthropic_model = ModelConfig.model_validate(
        {
            **config.models["luna"].model_dump(),
            "provider": "anthropic",
            "model": "claude-test",
            "settings": {},
        }
    )
    anthropic = DirectProvider(
        anthropic_model,
        config.budgets,
        client=httpx.Client(transport=httpx.MockTransport(completed)),
    )
    anthropic.send(anthropic.request(ctx, []))
    assert "prompt_cache_options" not in anthropic.request(ctx, [])
