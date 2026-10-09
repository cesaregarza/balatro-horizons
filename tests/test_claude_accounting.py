import pytest
from pydantic import ValidationError
from test_claude_support import claude

from balatro_horizons.config import Limits, ModelConfig
from balatro_horizons.harness.money import reservation_usd
from balatro_horizons.harness.transport import DirectProvider


@pytest.mark.parametrize("name,rates", [
    ("Fable 5.1", (10, 50, 12.5, 0.25)), ("Opus 5.5", (4, 20, 5, 0.20)),
    ("Sonnet 5.5", (2, 10, 2.5, 0.10)), ("Haiku 5.5", (0.10, 0.50, 0.125, 0.01)),
])
def test_preset_prices_and_reservations_cover_cache_write_premium(name, rates):
    model = claude(name)
    assert model.pricing_date == "2026-10-08"
    assert (model.input_usd_per_million, model.output_usd_per_million,
            model.cache_write_input_usd_per_million, model.cached_input_usd_per_million) == rates
    assert reservation_usd(model, Limits()) == pytest.approx(32768 * (rates[1] + rates[2]) / 1e6)


def test_usage_is_disjoint_and_thinking_is_already_in_output():
    policy = DirectProvider(claude(), Limits())
    usage = {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 1000,
             "cache_creation_input_tokens": 200, "output_tokens_details": {"thinking_tokens": 40},
             "cache_creation": {"ephemeral_5m_input_tokens": 200, "ephemeral_1h_input_tokens": 0},
             "service_tier": "standard", "inference_geo": "global"}
    assert policy.usage_cost({"usage": usage}, 1) == pytest.approx(0.0013)
    # Cache misses and models below the minimum cacheable length still bill ordinary input.
    assert policy.usage_cost({"usage": {"input_tokens": 100, "output_tokens": 50}}, 1) == pytest.approx(0.0007)


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"])
@pytest.mark.parametrize("bad", [None, True, -1, "2", 1.5, [], {}])
def test_unknown_or_malformed_token_categories_keep_reservation(field, bad):
    usage = {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 1000,
             "cache_creation_input_tokens": 200, field: bad}
    assert DirectProvider(claude(), Limits()).usage_cost({"usage": usage}, 0.25) == 0.25


@pytest.mark.parametrize("extra", [
    {"service_tier": "priority"}, {"inference_geo": "us"},
    {"cache_creation": []}, {"cache_creation": {}},
    {"cache_creation": {"ephemeral_5m_input_tokens": 199, "ephemeral_1h_input_tokens": 1}},
    {"cache_creation": {"ephemeral_5m_input_tokens": 201, "ephemeral_1h_input_tokens": 0}},
    {"cache_creation": {"ephemeral_5m_input_tokens": 200, "ephemeral_1h_input_tokens": False}},
])
def test_unconfigured_pricing_and_inconsistent_write_details_keep_reservation(extra):
    usage = {"input_tokens": 100, "output_tokens": 50, "cache_creation_input_tokens": 200, **extra}
    assert DirectProvider(claude(), Limits()).usage_cost({"usage": usage}, 0.25) == 0.25


def test_haiku_usage_cannot_silently_cross_its_price_tier():
    usage = {"input_tokens": 1, "output_tokens": 50, "cache_read_input_tokens": 100_000}
    assert DirectProvider(claude("Haiku 5.5"), Limits()).usage_cost({"usage": usage}, 0.25) == 0.25


def test_no_cache_pricing_preserves_legacy_conservative_accounting():
    values = claude().model_dump()
    values.update(cached_input_usd_per_million=None, cache_write_input_usd_per_million=None)
    policy = DirectProvider(ModelConfig.model_validate(values), Limits())
    usage = {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 1000}
    assert policy.usage_cost({"usage": usage}, 1) == pytest.approx(0.0027)
    assert policy.usage_cost({"usage": {**usage, "cache_creation_input_tokens": 1}}, 1) == 1
    assert policy.usage_cost({}, 1) == 1
    values["cached_input_usd_per_million"] = 0.1
    with pytest.raises(ValidationError, match="both cache read and write rates"):
        ModelConfig.model_validate(values)
