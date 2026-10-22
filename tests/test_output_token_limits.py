"""Output headroom is a ceiling, not billed usage; all providers are offline."""

import json

import httpx
import pytest
from test_boundary import project
from test_providers_evaluation import model

from balatro_horizons.config import PROVIDER_TIMEOUT_SECONDS, Config, Limits
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.harness.baselines import Baseline
from balatro_horizons.harness.context.build import context
from balatro_horizons.harness.context.freeze import freeze_protocol, validate_continuation
from balatro_horizons.harness.money import BudgetExhausted, Spending, reservation_usd
from balatro_horizons.harness.transport import DirectProvider


@pytest.mark.parametrize(
    "provider,field", [("openai", "max_output_tokens"), ("anthropic", "max_tokens")]
)
def test_output_default_and_override_change_only_request_ceiling(provider, field):
    ctx = context(project(FakeGame().observe_private()))
    bodies = []
    for limits, expected in [(Limits(), 32_768), (Limits(max_output_tokens_per_call=8192), 8192)]:
        policy = DirectProvider(model(provider), limits)
        try:
            body = policy.request(ctx, [])
            assert body.pop(field) == expected
            bodies.append(body)
        finally:
            policy.client.close()
    assert bodies[0] == bodies[1]


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("output_limit", [None, 64, 8192, 65_536])
def test_stream_read_timeout_bounds_stalls_not_total_output_time(provider, output_limit):
    limits = Limits() if output_limit is None else Limits(max_output_tokens_per_call=output_limit)
    policy = DirectProvider(model(provider), limits)
    try:
        request = policy.client.build_request("POST", "https://example.invalid", json={})
        assert request.extensions["timeout"] == {
            name: PROVIDER_TIMEOUT_SECONDS for name in ("connect", "read", "write", "pool")
        }
        assert policy.request(context(project(FakeGame().observe_private())), [])["stream"] is True
    finally:
        policy.client.close()


def test_injected_client_keeps_its_explicit_timeout():
    with httpx.Client(timeout=7, trust_env=False) as client:
        policy = DirectProvider(model("openai"), Limits(), client=client)
        assert policy.client is client
        assert policy.client.timeout.as_dict() == {
            "connect": 7, "read": 7, "write": 7, "pool": 7,
        }


@pytest.mark.parametrize("provider,reserve", [("openai", 0.106496), ("anthropic", 0.098304)])
def test_larger_reservation_still_settles_only_reported_usage(provider, reserve, tmp_path):
    policy = DirectProvider(model(provider), Limits())
    try:
        assert reservation_usd(policy.model, policy.limits) == pytest.approx(reserve)
        spending = Spending(tmp_path / "spending.json", reserve)
        spending.reserve("first", "episode", reserve, reserve)
        with pytest.raises(BudgetExhausted, match="EPISODE_AND_CAMPAIGN_COST_CAP"):
            spending.reserve("second", "episode", reserve, reserve)
        actual = policy.usage_cost({"usage": {
            "input_tokens": 1000, "output_tokens": 200,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 150},
        }}, reserve)
        assert actual == pytest.approx(0.0014)
        spending.settle("first", actual)
        entry = json.loads(spending.path.read_text())["first"]
        assert entry["settled"] and entry["cost"] == pytest.approx(0.0014)
        assert entry["reserved"] == pytest.approx(reserve)
    finally:
        policy.client.close()


def test_explicit_old_limit_survives_serialization_and_cannot_be_silently_widened():
    old = Config(budgets=Limits(max_output_tokens_per_call=8192))
    restored = Config.model_validate_json(old.model_dump_json())
    assert restored.budgets.max_output_tokens_per_call == 8192
    frozen = freeze_protocol(old, Baseline("heuristic"), {"skills": []})
    validate_continuation(frozen, restored, "heuristic")
    with pytest.raises(ValueError, match="AGENT_PROTOCOL_CONFIGURATION_CHANGED"):
        validate_continuation(frozen, Config(), "heuristic")
    assert frozen["episode_limits"]["max_output_tokens_per_call"] == 8192
