"""GPT-6 Sol/Luna configuration contracts; no provider or native calls."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_boundary import project

from balatro_horizons.cli.register_player import settings_payload
from balatro_horizons.cli.smoke import _load_smoke_config
from balatro_horizons.config import ROOT, Limits, ModelConfig, load_config
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.context.build import context
from balatro_horizons.harness.transport import (
    DirectProvider,
    ProviderFailure,
    public_capability_table,
)

EFFORTS = ["none", "low", "medium", "high", "xhigh", "max"]


@pytest.fixture(params=["sol", "luna"])
def configured(request):
    tier = request.param
    config = load_config(ROOT / f"configs/gpt6-{tier}-smoke.yaml")
    return tier, config, config.models[f"{tier}6"]


def test_config_pricing_capabilities_and_unpaid_smoke(configured):
    tier, config, model = configured
    rates = (2, 0.2, 2.5, 10) if tier == "sol" else (0.1, 0.01, 0.125, 0.5)
    assert model.model == f"gpt-6-{tier}"
    assert (
        model.input_usd_per_million,
        model.cached_input_usd_per_million,
        model.cache_write_input_usd_per_million,
        model.output_usd_per_million,
    ) == rates
    assert model.pricing_date == "2026-09-22"
    assert model.settings == {"reasoning_effort": "medium", "reasoning_summary": "auto"}
    assert not config.budgets.paid_calls_enabled and not config.workbench_enabled
    assert (config.budgets.max_episode_cost_usd, config.budgets.max_batch_cost_usd) == (1, 5)
    declared = public_capability_table(config.models)["models"][f"model:openai:gpt-6-{tier}"]
    assert declared["display_name"] == f"GPT-6 {tier.title()}"
    assert declared["reasoning_efforts"] == EFFORTS
    assert declared["unsupported_settings"] == {"reasoning_effort": ["minimal"]}
    assert declared["prompt_cache_diagnostics"] and declared["explicit_cache_mode"]
    smoke, _, _ = _load_smoke_config(
        SimpleNamespace(
            config=ROOT / f"configs/gpt6-{tier}-smoke.yaml",
            agent=f"{tier}6",
            authorized_episode_cap=1,
            authorized_total_cap=5,
            allow_paid=False,
        )
    )
    assert smoke == config


@pytest.mark.parametrize("effort", EFFORTS)
def test_each_effort_uses_standard_stateless_responses(configured, effort):
    _, config, model = configured
    model = ModelConfig.model_validate(
        {
            **model.model_dump(),
            "settings": {**model.settings, "reasoning_effort": effort},
        }
    )
    policy = DirectProvider(model, config.budgets)
    body = policy.request(context(project(FakeGame().observe_private())), [])
    assert body["model"] == model.model
    assert body["reasoning"] == {"effort": effort, "summary": "auto"}
    assert body["service_tier"] == "default" and body["store"] is False
    assert body["prompt_cache_options"] == {"mode": "explicit"}
    assert body["truncation"] == "disabled"
    assert body["max_output_tokens"] == config.budgets.max_output_tokens_per_call
    assert body["parallel_tool_calls"] is False
    assert "temperature" not in body and "previous_response_id" not in body
    assert {tool["name"] for tool in body["tools"]} >= {"select_blind", "inspect_state"}


@pytest.mark.parametrize("effort", [None, "low", "medium", "high", "xhigh", "max"])
def test_reasoning_temperature_rejected_before_send(configured, effort):
    _, _, model = configured
    settings = {"temperature": 0.5}
    if effort is not None:
        settings["reasoning_effort"] = effort
    with pytest.raises(ValidationError, match="temperature only with reasoning_effort none"):
        ModelConfig.model_validate({**model.model_dump(), "settings": settings})


def test_minimal_rejected_and_nonreasoning_temperature_supported(configured):
    _, config, model = configured
    with pytest.raises(ValidationError, match="does not support minimal"):
        ModelConfig.model_validate(
            {
                **model.model_dump(),
                "settings": {"reasoning_effort": "minimal"},
            }
        )
    model = ModelConfig.model_validate(
        {
            **model.model_dump(),
            "settings": {"reasoning_effort": "none", "temperature": 0.5},
        }
    )
    body = DirectProvider(model, config.budgets).request(
        context(project(FakeGame().observe_private())),
        [],
    )
    assert body["temperature"] == 0.5 and body["reasoning"]["effort"] == "none"


def test_cache_category_cost_and_long_context_refusal(configured):
    tier, config, model = configured
    policy = DirectProvider(model, config.budgets)
    response = {
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 200,
            "input_tokens_details": {"cached_tokens": 300, "cache_write_tokens": 200},
            "output_tokens_details": {"reasoning_tokens": 150},
        }
    }
    assert policy.usage_cost(response, 1) == pytest.approx(
        0.00356 if tier == "sol" else 0.000178,
    )
    assert policy.usage_cost({"usage": {"input_tokens": 1000, "output_tokens": 200}}, 1) == 1
    policy = DirectProvider(model, Limits(max_input_tokens_per_call=272001))
    with pytest.raises(ProviderFailure, match="CACHE_LONG_CONTEXT_PRICING_NOT_CONFIGURED"):
        policy.request(context(project(FakeGame().observe_private())), [])


def test_registration_is_additive_preserving_existing_defaults(configured):
    tier, _, model = configured
    original = load_config(ROOT / "configs/luna-smoke.yaml").public()
    before = deepcopy(original)
    payload = settings_payload(original, f"{tier}6", model.model_dump())
    assert original == before
    assert payload["models"] == {**before["models"], f"{tier}6": model.model_dump()}
    assert payload["budgets"] == before["budgets"]
    assert payload["skills"] == before["skills"]
