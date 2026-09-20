import json

import httpx
import pytest
from provider_transport import with_input_count
from pydantic import ValidationError
from test_boundary import project
from test_providers_evaluation import model

from balatro_horizons.agents.budget import BudgetExhausted, Spending
from balatro_horizons.agents.protocol import context
from balatro_horizons.agents.providers import DirectProvider, ProviderFailure
from balatro_horizons.config import Limits, ModelConfig
from balatro_horizons.evaluation.batches import summarize
from balatro_horizons.evidence.certification import (
    require_checkpoint_certificate,
    verify_checkpoint,
)
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.game.state import normalize
from balatro_horizons.review.service import ReviewService
from balatro_horizons.storage.journal import Store


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_direct_http_transport_and_unknown_usage(provider, monkeypatch):
    key_name = "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY"
    monkeypatch.setenv(key_name, "test-private-token")
    requests = []

    def receive(request):
        requests.append(request)
        return httpx.Response(503, json={"message": "private upstream details"})

    policy = DirectProvider(
        model(provider), Limits(), client=httpx.Client(transport=httpx.MockTransport(with_input_count(receive)))
    )
    body = policy.request(context(project(FakeGame().observe_private())), [])
    with pytest.raises(ProviderFailure, match="PROVIDER_HTTP_503") as error:
        policy.send(body)
    assert error.value.retryable
    assert requests[0].url.host == (
        "api.openai.com" if provider == "openai" else "api.anthropic.com"
    )
    assert "test-private-token" not in json.dumps(body)
    assert policy.usage_cost({}, 0.25) == 0.25


def test_reported_overage_is_not_hidden(tmp_path):
    policy = DirectProvider(model("openai"), Limits())
    actual = policy.usage_cost(
        {
            "usage": {
                "input_tokens": 1_000_000,
                "output_tokens": 1_000_000,
                "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            }
        },
        0.5,
    )
    assert actual == 3
    spending = Spending(tmp_path / "spending.json", 1)
    spending.reserve("a", "episode", 0.5, 1)
    spending.settle("a", actual)
    with pytest.raises(BudgetExhausted):
        spending.reserve("b", "episode", 0.01, 1)


@pytest.mark.parametrize(
    "change",
    [
        {"input_usd_per_million": 0},
        {"output_usd_per_million": float("nan")},
        {"pricing_date": "2026-99-14"},
        {"model": "  "},
        {"settings": {"temperature": "secret"}},
        {"settings": {"reasoning_effort": "unlimited"}},
    ],
)
def test_invalid_pricing_and_settings_fail_closed(change):
    data = model("openai").model_dump()
    with pytest.raises(ValidationError):
        ModelConfig.model_validate({**data, **change})


def test_recovery_uses_settled_cost_and_retains_unknown(store):
    eid = store.create({"evidence_kind": "SYNTHETIC_TEST"})
    store.append(eid, "provider_request", {"reserved_usd": 1}, request_id="known")
    store.append(eid, "provider_response", {"cost_usd": 0.2}, request_id="known")
    store.append(eid, "provider_request", {"reserved_usd": 1}, request_id="unknown")
    store.recover()
    assert store.summary(eid)["cost_usd"] == 1.2


def test_journal_head_cache_detects_other_writer(store):
    eid = store.create({"evidence_kind": "SYNTHETIC_TEST"})
    store.append(eid, "first", {})
    other = Store(store.root)
    other.append(eid, "second", {})
    store.append(eid, "third", {})
    assert [e["sequence"] for e in store.events(eid)] == [0, 1, 2]


def test_no_zero_repeat_certification_and_immutable_records(store, episode, config):
    with pytest.raises(ValueError, match="AT_LEAST_THREE"):
        verify_checkpoint(store, config, episode, 0, repetitions=0)
    first = verify_checkpoint(store, config, episode, 0)
    verify_checkpoint(store, config, episode, 0)
    path = store.episode_path(episode, True) / (
        "certificate-record-" + first["certificate_id"] + ".json"
    )
    assert json.loads(path.read_text()) == first
    require_checkpoint_certificate(store, episode, 0)


def test_trajectory_never_includes_unrevealed_state(store, episode):
    review = ReviewService(store)
    opened = review.open(episode)
    assert [p["decision"] for p in opened["view"]["trajectory"]] == [0]
    review.advance(opened["review_token"])
    after = review.advance(opened["review_token"])
    assert [p["decision"] for p in after["trajectory"]] == [0, 1]


def test_matched_missing_pairs_include_both_missing():
    plan = {
        "batch_id": "test",
        "slots": [
            {"slot_id": a, "seed_group": "seed", "replicate": 0, "agent": a} for a in ("a", "b")
        ],
    }
    comparison = summarize(plan, [])["paired_comparisons"][0]
    assert comparison["missing_pairs"] == 1 and comparison["matched_pairs"] == 0


def test_hidden_consumable_capabilities_do_not_leak():
    raw = {
        "state": "SELECTING_HAND",
        "bh": {},
        "consumables": {
            "cards": [{"id": 1, "state": {"hidden": True}, "usable": False, "sellable": False}]
        },
    }
    left = project(normalize(raw))
    raw["consumables"]["cards"][0].update(
        {"usable": True, "sellable": True, "counters": {"secret": 100}}
    )
    assert project(normalize(raw)) == left
