"""One preset list for every declared model, and each model's rules; no provider calls."""

import pytest
from pydantic import ValidationError
from test_boundary import project

from balatro_horizons.config import Config, Limits, ModelConfig
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.context.build import decision_context
from balatro_horizons.harness.transport import (
    DirectProvider,
    public_capability_table,
    validate_settings,
)

NONE_TO_MAX = ["none", "low", "medium", "high", "xhigh", "max"]
LOW_TO_MAX = NONE_TO_MAX[1:]
# Name, provider, model, (input, output, cache write, cache read), preset effort.
PRESETS = [
    ("Claude Fable 5.2", "anthropic", "claude-fable-5-2", (8, 40, 10, 0.20), "high"),
    ("Claude Fable 5.1", "anthropic", "claude-fable-5-1", (10, 50, 12.5, 0.25), "high"),
    ("Claude Opus 5.5", "anthropic", "claude-opus-5-5", (4, 20, 5, 0.20), "medium"),
    ("Claude Sonnet 5.5", "anthropic", "claude-sonnet-5-5", (2, 10, 2.5, 0.10), "high"),
    ("Claude Haiku 5.5", "anthropic", "claude-haiku-5-5", (0.10, 0.50, 0.125, 0.01), "medium"),
    ("GPT-6.2 Astra", "openai", "gpt-6.2-astra", (8, 40, 10, 0.40), "medium"),
    ("GPT-6.2 Sol", "openai", "gpt-6.2-sol", (2, 10, 2.5, 0.10), "medium"),
    ("GPT-6.1 Terra", "openai", "gpt-6.1-terra", (1, 5, 1.25, 0.05), "medium"),
    ("GPT-6.1 Sol", "openai", "gpt-6.1-sol", (2, 10, 2.5, 0.10), "medium"),
    ("GPT-6 Sol", "openai", "gpt-6-sol", (2, 10, 2.5, 0.20), "medium"),
    ("GPT-6 Luna", "openai", "gpt-6-luna", (0.10, 0.50, 0.125, 0.01), "medium"),
]
# Model: display name, efforts, refused efforts, temperature rule.
NEW_OPENAI = {
    "gpt-6.2-astra": ("GPT-6.2 Astra", LOW_TO_MAX, ["minimal", "none"], "never"),
    "gpt-6.2-sol": ("GPT-6.2 Sol", LOW_TO_MAX, ["minimal", "none"], "never"),
    "gpt-6.1-terra": ("GPT-6.1 Terra", NONE_TO_MAX, ["minimal"], "with none"),
    "gpt-6.1-sol": ("GPT-6.1 Sol", LOW_TO_MAX, ["minimal", "none"], "never"),
}
NEVER_TEMPERATURE = [model for model, row in NEW_OPENAI.items() if row[3] == "never"]
ALL_EFFORTS = [None, "none", "minimal", "low", "medium", "high", "xhigh", "max"]
EVERY_EFFORT = ALL_EFFORTS[1:]
# Today's behavior, recorded before these models were added. Each row: display name,
# efforts offered when "high" is pinned, refused efforts, efforts that accept temperature.
EXISTING_OPENAI = {
    "gpt-5.6-luna": ("GPT-5.6 Luna", NONE_TO_MAX, ["minimal"], [None, "none", *LOW_TO_MAX]),
    "gpt-5.6-terra": ("GPT-5.6 Terra", NONE_TO_MAX, [], ALL_EFFORTS),
    "gpt-5.6-sol": ("GPT-5.6 Sol", NONE_TO_MAX, [], ALL_EFFORTS),
    "gpt-5.6-astra": ("GPT-5.6 Astra", NONE_TO_MAX, [], ALL_EFFORTS),
    "gpt-5.6-test": ("gpt-5.6-test", NONE_TO_MAX, [], ALL_EFFORTS),
    "gpt-6-luna": ("GPT-6 Luna", NONE_TO_MAX, ["minimal"], ["none"]),
    "gpt-6-terra": ("GPT-6 Terra", ["high"], [], ALL_EFFORTS),
    "gpt-6-sol": ("GPT-6 Sol", NONE_TO_MAX, ["minimal"], ["none"]),
    "gpt-6-astra": ("GPT-6 Astra", LOW_TO_MAX, [], ALL_EFFORTS),
    "gpt-6-astra-law": ("gpt-6-astra-law", LOW_TO_MAX, [], ALL_EFFORTS),
    "gpt-6.2-astra-law": ("gpt-6.2-astra-law", ["high"], [], ALL_EFFORTS),
    "gpt-5.5": ("gpt-5.5", ["high"], [], ALL_EFFORTS),
}


def presets():
    return Config().public()["model_presets"]


def openai(model, **settings):
    return ModelConfig(
        provider="openai",
        model=model,
        input_usd_per_million=1,
        output_usd_per_million=2,
        cached_input_usd_per_million=0.1,
        cache_write_input_usd_per_million=1.25,
        pricing_date="2026-11-22",
        settings=settings,
    )


def capabilities(model):
    return public_capability_table({"m": model})["models"][f"model:{model.provider}:{model.model}"]


def context():
    return decision_context(project(FakeGame().observe_private()), [])[0]


def effort(value):
    return {} if value is None else {"reasoning_effort": value}


def accepts(provider, model, settings):
    try:
        validate_settings(provider, model, settings)
    except ValueError:
        return False
    return True


def outcomes(provider, model):
    """Refused efforts, and efforts that accept a temperature."""
    return (
        [value for value in ALL_EFFORTS if not accepts(provider, model, effort(value))],
        [
            value
            for value in ALL_EFFORTS
            if accepts(provider, model, {**effort(value), "temperature": 0.5})
        ],
    )


def test_one_list_offers_every_model_by_name_and_provider():
    assert [(name, value["provider"], value["model"]) for name, value in presets().items()] == [
        (name, provider, model) for name, provider, model, _, _ in PRESETS
    ]


@pytest.mark.parametrize("name,provider,model,rates,preset_effort", PRESETS)
def test_preset_fills_provider_model_all_four_prices_and_effort(
    name,
    provider,
    model,
    rates,
    preset_effort,
):
    assert presets().get(name) == {
        "provider": provider,
        "model": model,
        "input_usd_per_million": rates[0],
        "output_usd_per_million": rates[1],
        "cache_write_input_usd_per_million": rates[2],
        "cached_input_usd_per_million": rates[3],
        "pricing_date": "2026-11-22",
        "settings": {"reasoning_effort": preset_effort},
    }


@pytest.mark.parametrize("name,provider,model,rates,preset_effort", PRESETS)
def test_every_preset_is_launchable_with_its_effort(name, provider, model, rates, preset_effort):
    available = presets()
    assert name in available
    chosen = ModelConfig.model_validate(available[name])
    declared = capabilities(chosen)
    assert declared["display_name"] == name
    assert declared["prompt_cache_diagnostics"] and declared["explicit_cache_mode"]
    assert preset_effort in declared["reasoning_efforts"]
    body = DirectProvider(chosen, Limits()).request(context(), [])
    assert body["model"] == model
    sent = body["output_config"] if provider == "anthropic" else body["reasoning"]
    assert sent["effort"] == preset_effort
    assert "temperature" not in body


def test_choosing_a_preset_never_changes_the_next_list():
    first = presets()
    first["GPT-6.2 Sol"] = None
    assert presets().get("GPT-6.2 Sol", {}).get("model") == "gpt-6.2-sol"


@pytest.mark.parametrize("model", NEW_OPENAI)
def test_new_openai_model_declares_name_efforts_and_refusals(model):
    name, efforts, refused, _ = NEW_OPENAI[model]
    declared = capabilities(openai(model))
    assert declared["display_name"] == name
    assert declared["reasoning_efforts"] == efforts
    assert declared["unsupported_settings"] == {"reasoning_effort": refused}
    for value in efforts:
        body = DirectProvider(openai(model, reasoning_effort=value), Limits()).request(
            context(), []
        )
        assert body["reasoning"]["effort"] == value


@pytest.mark.parametrize(
    "model,value", [(model, value) for model, row in NEW_OPENAI.items() for value in row[2]]
)
def test_new_openai_model_refuses_unsupported_effort_before_a_request(model, value):
    with pytest.raises(ValidationError, match=f"{model} does not support {value} reasoning_effort"):
        openai(model, reasoning_effort=value)


@pytest.mark.parametrize("model", NEVER_TEMPERATURE)
@pytest.mark.parametrize("value", [None, *LOW_TO_MAX])
def test_models_without_temperature_refuse_it_at_every_effort(model, value):
    with pytest.raises(ValidationError, match=f"{model} does not support temperature"):
        openai(model, temperature=0.5, **effort(value))


@pytest.mark.parametrize("value", [None, *LOW_TO_MAX])
def test_terra_refuses_temperature_unless_effort_is_none(value):
    with pytest.raises(ValidationError, match="temperature only with reasoning_effort none"):
        openai("gpt-6.1-terra", temperature=0.5, **effort(value))


def test_terra_sends_temperature_with_none_effort():
    model = openai("gpt-6.1-terra", reasoning_effort="none", temperature=0.5)
    body = DirectProvider(model, Limits()).request(context(), [])
    assert body["temperature"] == 0.5 and body["reasoning"]["effort"] == "none"
    assert capabilities(model)["reasoning_efforts"] == NONE_TO_MAX


def test_fable_5_2_uses_adaptive_thinking_and_no_haiku_input_tier():
    model = ModelConfig.model_validate(
        {
            "provider": "anthropic",
            "model": "claude-fable-5-2",
            "input_usd_per_million": 8,
            "output_usd_per_million": 40,
            "cached_input_usd_per_million": 0.2,
            "cache_write_input_usd_per_million": 10,
            "pricing_date": "2026-11-22",
        }
    )
    body = DirectProvider(model, Limits(max_input_tokens_per_call=100_001)).request(context(), [])
    assert body.get("thinking") == {"type": "adaptive", "display": "summarized"}
    assert body.get("output_config") == {"effort": "high"}


@pytest.mark.parametrize("model", EXISTING_OPENAI)
def test_existing_openai_model_keeps_todays_rules(model):
    name, efforts, refused, temperature = EXISTING_OPENAI[model]
    declared = capabilities(openai(model, reasoning_effort="high"))
    assert declared["display_name"] == name
    assert declared["reasoning_efforts"] == efforts
    assert declared["unsupported_settings"] == ({"reasoning_effort": refused} if refused else {})
    assert outcomes("openai", model) == (refused, temperature)


@pytest.mark.parametrize(
    "model,refused",
    [
        ("claude-fable-5-1", ["none", "minimal"]),
        ("claude-opus-5-5", ["none", "minimal"]),
        ("claude-sonnet-5-5", ["none", "minimal"]),
        ("claude-haiku-5-5", ["none", "minimal"]),
        ("claude-legacy", EVERY_EFFORT),
    ],
)
def test_existing_claude_model_keeps_todays_rules(model, refused):
    assert outcomes("anthropic", model) == (refused, [] if model != "claude-legacy" else [None])
