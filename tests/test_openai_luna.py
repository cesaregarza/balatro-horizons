
"""Contract tests use mocked HTTP and the explicitly synthetic game only."""

import json

import httpx
import pytest
from provider_transport import with_input_count
from pydantic import ValidationError
from test_boundary import project

from balatro_horizons.config import ROOT, Limits, ModelConfig, load_config
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.baselines import Baseline
from balatro_horizons.harness.context.build import context
from balatro_horizons.harness.loop import Runner
from balatro_horizons.harness.money import Spending
from balatro_horizons.harness.transport import DirectProvider, ProtocolFailure, ProviderFailure
from balatro_horizons.review.service import ReviewService


def luna():
    return load_config(ROOT / "configs/luna-smoke.yaml")


def response(operation):
    kind = operation.get("kind")
    if kind == "action":
        envelope = operation["envelope"]
        action = dict(envelope["action"])
        name = action.pop("type")
        arguments = {
            **action,
            "observation_id": envelope["observation_id"],
            "decision_note": envelope.get("decision_note"),
            "note_update": operation.get("note_update"),
        }
    elif kind == "arithmetic":
        name, arguments = "calculate", {"expression": operation["expression"]}
    elif kind == "abort":
        name, arguments = "abort_run", {"reason": operation["reason"]}
    elif kind == "inspect_page":
        name = "inspect_state"
        arguments = {key: operation[key] for key in ("section", "offset")}
    elif kind == "skill":
        name, arguments = "read_skill", {"name": operation["name"]}
    elif kind == "rules":
        name, arguments = "read_rules", {"key": operation["key"]}
    else:
        name, arguments = "calculate", {"expression": "0"}
    return {
        "model": "gpt-5.6-luna",
        "status": "completed",
        "service_tier": "default",
        "output": [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "MOCK_SUMMARY"}]},
            {
                "type": "function_call",
                "call_id": "mock_call",
                "name": name,
                "status": "completed",
                "arguments": json.dumps(arguments),
            },
        ],
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 200,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
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
    assert {tool["name"] for tool in body["tools"]} >= {
        "select_blind",
        "inspect_state",
        "abort_run",
    }
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
        message = next(item for item in body["input"] if item.get("role") == "user")
        ctx = json.loads(message["content"])
        if len(received) == 1:
            op = {"kind": "arithmetic", "expression": "2+2"}
        else:
            op = baseline.decide(ctx, [])
            if op["kind"] == "action":
                op["envelope"]["decision_note"] = "MOCK_NOTE"
                op["note_update"] = {"key": "memory", "text": "MOCK_MEMORY"}
        return httpx.Response(200, json=response(op))

    policy = DirectProvider(
        config.models["luna"],
        config.budgets,
        client=httpx.Client(transport=httpx.MockTransport(with_input_count(receive))),
    )
    summary = Runner(
        store,
        config,
        FakeGame(),
        policy,
        Spending.episode_only(
            store.root / "private_runs" / "test-spending.json",
            config.budgets.max_episode_cost_usd or 1,
        ),
    ).run()
    assert summary["outcome"] == "WIN" and summary["evidence_kind"] == "SYNTHETIC_TEST"
    assert summary["provider_calls"] > summary["committed_actions"] > 0
    assert summary["cost_usd"] == pytest.approx(len(received) * 0.00044)
    helper_output = next(
        item for item in received[1]["input"] if item.get("type") == "function_call_output"
    )
    assert json.loads(helper_output["output"])["result"] == "4"
    assert "MOCK_MEMORY" in json.dumps(received[2])
    assert "MOCK_SUMMARY" in json.dumps(received[1])
    assert "MOCK_SUMMARY" not in json.dumps(received[2])
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
    config.budgets.max_episode_cost_usd = 0.02  # one 0.0180224 reservation fits
    calls = []

    def receive(request):
        calls.append(request)
        raise httpx.ReadTimeout("mock timeout")

    policy = DirectProvider(
        config.models["luna"],
        config.budgets,
        client=httpx.Client(transport=httpx.MockTransport(with_input_count(receive))),
    )
    summary = Runner(
        store,
        config,
        FakeGame(),
        policy,
        Spending.episode_only(
            store.root / "private_runs" / "test-spending.json",
            config.budgets.max_episode_cost_usd or 1,
        ),
    ).run()
    assert summary["outcome"] == "BUDGET_EXHAUSTED"
    assert len(calls) == 1 and summary["committed_actions"] == 0
    assert summary["cost_usd"] == pytest.approx(0.0180224)


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
        client=httpx.Client(transport=httpx.MockTransport(with_input_count(receive))),
    )
    result = Runner(
        store,
        config,
        FakeGame(),
        policy,
        Spending.episode_only(
            store.root / "private_runs" / "test-spending.json",
            config.budgets.max_episode_cost_usd or 1,
        ),
    ).run()
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
        client=httpx.Client(transport=httpx.MockTransport(with_input_count(receive))),
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
    policy.request(context(project(FakeGame().observe_private())), [])
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
        client=httpx.Client(transport=httpx.MockTransport(with_input_count(completed))),
    )
    first = terra.request(ctx, [])
    assert first["prompt_cache_options"] == {"mode": "explicit"}
    terra.send(first)
    assert terra.request(ctx, [])["prompt_cache_options"] == {
        "mode": "explicit",
        "comparison_response_id": "resp_episode_1"
    }

    fresh_episode = DirectProvider(terra_model, config.budgets)
    assert fresh_episode.request(ctx, [])["prompt_cache_options"] == {"mode": "explicit"}

    unsupported_model = ModelConfig.model_validate(
        {**config.models["luna"].model_dump(), "model": "gpt-5.5"}
    )
    unsupported = DirectProvider(unsupported_model, config.budgets)
    with pytest.raises(ProviderFailure, match="EXPLICIT_CACHE_REQUIRES_GPT_5_6_OR_LATER"):
        unsupported.request(ctx, [])

    anthropic_model = ModelConfig.model_validate(
        {
            **config.models["luna"].model_dump(),
                "provider": "anthropic",
                "model": "claude-test",
                "cached_input_usd_per_million": None,
                "cache_write_input_usd_per_million": None,
                "settings": {},
        }
    )
    anthropic = DirectProvider(
        anthropic_model,
        config.budgets,
        client=httpx.Client(transport=httpx.MockTransport(with_input_count(completed))),
    )
    anthropic.send(anthropic.request(ctx, []))
    assert "prompt_cache_options" not in anthropic.request(ctx, [])
