"""Current Claude contracts; all HTTP is mocked and all games are synthetic."""

import json
from copy import deepcopy
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from provider_transport import model_choice, stream_response
from pydantic import ValidationError
from test_boundary import project
from test_openai_luna import response as openai_response
from test_provider_continuations import exchange

from balatro_horizons.api import create_app
from balatro_horizons.config import Config, Limits, ModelConfig
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.context.build import decision_context
from balatro_horizons.harness.loop import Runner
from balatro_horizons.harness.money import Spending
from balatro_horizons.harness.transport import DirectProvider, ProviderFailure
from balatro_horizons.harness.transport.claude_models import presets

NAMES = ("Fable 5.1", "Opus 5.5", "Sonnet 5.5", "Haiku 5.5")


def claude(name="Sonnet 5.5", **settings):
    value = presets()[f"Claude {name}"]
    if settings:
        value["settings"] = settings
    return ModelConfig.model_validate(value)


def context():
    return decision_context(project(FakeGame().observe_private()), [])[0]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_adaptive_request_preserves_v8_tools_and_caches_only_stable_prefix(name, effort):
    policy = DirectProvider(claude(name, reasoning_effort=effort), Limits())
    ctx = context()
    body = policy.request(ctx, [])
    assert policy.interface == "tools_v8"
    assert body["model"] == claude(name).model
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert body["output_config"] == {"effort": effort}
    assert body["service_tier"] == "standard_only" and body["max_tokens"] == 32_768
    assert body["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert all(tool["strict"] is False for tool in body["tools"])
    assert body["system"] == [{"type": "text", "text": ctx.prompt + "\n\n" + ctx.rules_kernel,
                               "cache_control": {"type": "ephemeral", "ttl": "5m"}}]
    assert json.dumps(body).count('"cache_control"') == 1
    assert "temperature" not in body and "budget_tokens" not in json.dumps(body)
    view = json.loads(body["messages"][0]["content"])
    assert type(view["observation"]["observation_id"]) is int
    assert body["system"] == policy.request(ctx, [])["system"]


@pytest.mark.parametrize("name,default", zip(NAMES, ("high", "medium", "high", "medium"), strict=True))
def test_explicit_model_defaults_and_public_capabilities(name, default):
    values = claude(name).model_dump()
    values["settings"] = {}
    model = ModelConfig.model_validate(values)
    policy = DirectProvider(model, Limits())
    assert policy.request(context(), [])["output_config"] == {"effort": default}
    public = Config(models={"claude": model}).public()
    caps = public["model_capabilities"]["models"][f"model:anthropic:{model.model}"]
    assert caps["display_name"] == f"Claude {name}"
    assert caps["default_reasoning_effort"] == default
    assert caps["reasoning_efforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert caps["supported_settings"] == ["reasoning_effort"]
    assert caps["prompt_cache_diagnostics"] and caps["explicit_cache_mode"]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("settings", [
    {"reasoning_effort": "none"}, {"reasoning_effort": "minimal"},
    {"reasoning_effort": None}, {"reasoning_effort": True}, {"reasoning_effort": []},
    {"thinking_budget": 1024}, {"temperature": 1}, {"top_p": 1},
])
def test_unsupported_modern_settings_are_rejected_before_transport(name, settings):
    with pytest.raises(ValidationError):
        claude(name, **settings)


def test_legacy_manual_thinking_and_uncached_wire_format_remain_available():
    values = claude().model_dump()
    values.update(model="claude-legacy", cached_input_usd_per_million=None,
                  cache_write_input_usd_per_million=None, settings={"thinking_budget": 1024})
    model = ModelConfig.model_validate(values)
    body = DirectProvider(model, Limits()).request(context(), [])
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    assert isinstance(body["system"], str) and "output_config" not in body
    assert "cache_control" not in json.dumps(body)
    with pytest.raises(ProviderFailure, match="^THINKING_BUDGET_MUST_BE_BELOW_OUTPUT_LIMIT$"):
        DirectProvider(model, Limits(max_output_tokens_per_call=1024)).request(context(), [])
    with pytest.raises(ValidationError, match="declared adaptive-thinking"):
        ModelConfig.model_validate({**values, "settings": {"reasoning_effort": "high"}})


def test_unpriced_long_context_is_refused_before_count_or_generation():
    model = claude("Haiku 5.5")
    assert DirectProvider(model, Limits(max_input_tokens_per_call=100_000)).request(context(), [])
    with pytest.raises(ProviderFailure, match="^CLAUDE_LONG_CONTEXT_PRICING_NOT_CONFIGURED$"):
        DirectProvider(model, Limits(max_input_tokens_per_call=100_001)).request(context(), [])


def test_signed_thinking_count_and_tool_result_are_exact_with_caching(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "mock-only")
    requests = []

    def receive(request):
        requests.append((request.url.path, json.loads(request.content)))
        assert request.headers["x-api-key"] == "mock-only"
        assert request.headers["anthropic-version"] == "2023-06-01"
        return httpx.Response(200, json={"input_tokens": 200})

    policy = DirectProvider(claude(), Limits(), httpx.Client(transport=httpx.MockTransport(receive)))
    ctx = context()
    first = policy.request(ctx, [])
    blocks = [
        {"type": "thinking", "thinking": "inspect first", "signature": "signed-state"},
        {"type": "redacted_thinking", "data": "redacted-state"},
        {"type": "text", "text": "Checking."},
        {"type": "tool_use", "id": "toolu_one", "name": "calculate", "input": {"expression": "2+2"}},
    ]
    operation = policy.parse({"stop_reason": "tool_use", "content": deepcopy(blocks)})
    ctx, delivered = decision_context(
        project(FakeGame().observe_private()), [exchange(policy, operation, {"result": "4"})],
    )
    followup = policy.request(ctx, delivered)
    assert followup["system"] == first["system"] and followup["tools"] == first["tools"]
    assert followup["messages"][-2] == {"role": "assistant", "content": blocks}
    assert followup["messages"][-1]["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_one", "content": '{"result":"4"}'},
    ]
    policy.check_input(followup)
    expected = {key: followup[key] for key in
                ("model", "messages", "system", "tools", "tool_choice", "thinking", "output_config")}
    assert requests == [("/v1/messages/count_tokens", expected)]
    policy.check_input(followup)
    assert len(requests) == 1
    changed = deepcopy(followup)
    changed["output_config"]["effort"] = "max"
    policy.check_input(changed)
    assert len(requests) == 2  # Effort participates in the count identity.


def test_claude_runner_uses_compact_actions_bills_cache_and_freezes_defaults(store, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "mock-secret-not-real")
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_mock_private")
    config = Config(models={"claude": claude()})
    config.budgets.paid_calls_enabled = True
    config.budgets.max_episode_cost_usd, config.budgets.max_batch_cost_usd = 5.0, 10.0
    received, counted = [], []

    def receive(request):
        assert request.headers["anthropic-workspace-id"] == "wrkspc_mock_private"
        assert b"wrkspc_mock_private" not in request.content
        body = json.loads(request.content)
        if request.url.path.endswith("/count_tokens"):
            counted.append(body)
            return httpx.Response(200, json={"input_tokens": 900})
        received.append(body)
        view = json.loads(body["messages"][0]["content"])
        operation = {"kind": "arithmetic", "expression": "2+2"} if len(received) == 1 else model_choice(view)
        tool = openai_response(operation)["output"][-1]
        return stream_response({"stop_reason": "tool_use", "content": [
            {"type": "thinking", "thinking": "test", "signature": "opaque-signature"},
            {"type": "tool_use", "id": "toolu_current", "name": tool["name"], "input": json.loads(tool["arguments"])},
        ], "usage": {"input_tokens": 100, "output_tokens": 50,
                     "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 200}}, "anthropic")

    policy = DirectProvider(config.models["claude"], config.budgets,
                            httpx.Client(transport=httpx.MockTransport(receive)))
    policy.name = "claude"
    summary = Runner(store, config, FakeGame(), policy,
                     Spending.episode_only(store.root / "spending.json", 5)).run()
    assert summary["outcome"] == "WIN" and summary["evidence_kind"] == "SYNTHETIC_TEST"
    assert len(received) == len(counted) == summary["provider_calls"]
    assert summary["provider_calls"] > summary["committed_actions"] > 0
    assert summary["cost_usd"] == pytest.approx(len(received) * 0.0013)
    assert "opaque-signature" in json.dumps(received[1])
    assert "opaque-signature" not in json.dumps(received[2])
    assert all(body["system"] == received[0]["system"] for body in received)
    assert "mock-secret-not-real" not in json.dumps(store.events(summary["episode_id"]))
    assert "wrkspc_mock_private" not in json.dumps(store.events(summary["episode_id"]))
    assert "wrkspc_mock_private" not in (store.episode_path(summary["episode_id"], True)
                                        / "agent-protocol.json").read_text()
    frozen = json.loads((store.episode_path(summary["episode_id"], True)
                         / "agent-protocol.json").read_text())["model"]
    config.models["claude"].settings["reasoning_effort"] = "low"
    assert frozen["settings"] == {"reasoning_effort": "high"}
    assert frozen["cache_write_input_usd_per_million"] == 2.5


def test_presets_are_read_only_until_explicit_settings_save_and_run_effort_is_scoped(store):
    config = Config(models={"existing": claude("Opus 5.5")})
    config.budgets.max_episode_cost_usd, config.budgets.max_batch_cost_usd = 5.0, 10.0
    app = create_app(store.root, config)
    app.state.runs.start = Mock(return_value="a" * 32)
    with TestClient(app) as client:
        initial = client.get("/api/bootstrap").json()
        assert len(initial["config"]["model_presets"]) == 4
        assert list(initial["config"]["models"]) == ["existing"]
        assert not app.state.settings_path.exists()
        chosen = initial["config"]["model_presets"]["Claude Haiku 5.5"]
        headers = {"X-BH-Operator": initial["operator_token"]}
        saved = client.put("/api/settings", headers=headers, json={
            "models": {**initial["config"]["models"], "haiku": chosen},
            "budgets": initial["config"]["budgets"], "skills": initial["config"]["skills"],
        })
        assert saved.status_code == 200
        assert saved.json()["budgets"] == initial["config"]["budgets"]
        response = client.post("/api/runs", headers=headers, json={
            "agent": "haiku", "offline": True, "model_settings": {"reasoning_effort": "max"},
        })
        assert response.status_code == 200
        launched = app.state.runs.start.call_args.args[0]
        assert launched.models["haiku"].settings == {"reasoning_effort": "max"}
        assert app.state.config.models["haiku"].settings == {"reasoning_effort": "medium"}
        assert app.state.config.models["existing"] == config.models["existing"]
        assert not app.state.config.budgets.paid_calls_enabled
