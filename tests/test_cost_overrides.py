"""Explicit dollar overrides; synthetic games and mocked providers only."""

import json

import pytest
import test_campaign_budget
from fastapi.testclient import TestClient
from pydantic import ValidationError

from balatro_horizons.api import create_app
from balatro_horizons.api.models import RunInput
from balatro_horizons.config import Limits
from balatro_horizons.evidence.provenance import fingerprint_sources, source_files
from balatro_horizons.harness.money import (
    BudgetExhausted,
    PublicCostContext,
    Spending,
    validate_caps,
)

harness = test_campaign_budget.harness


@pytest.mark.parametrize("value", [False, True, 0, -1, float("inf"), float("nan"), "10", "", "Uncapped", 10**400, 1e300, 1_000_001])
def test_only_finite_positive_caps_or_explicit_uncapped_are_valid(value):
    with pytest.raises(ValidationError):
        Limits(max_episode_cost_usd=value)
    with pytest.raises(ValueError, match="PAID_EXECUTION_NOT_AUTHORIZED"):
        validate_caps(value, "uncapped")


def test_missing_limits_do_not_become_uncapped():
    limits = Limits()
    assert limits.max_episode_cost_usd is None
    with pytest.raises(ValueError, match="PAID_EXECUTION_NOT_AUTHORIZED"):
        validate_caps(None, "uncapped")
    assert not limits.paid_calls_enabled
    validate_caps("uncapped", "uncapped")
    assert Limits(max_episode_cost_usd="uncapped").model_dump()["max_episode_cost_usd"] == "uncapped"


def test_uncapped_ledger_still_reserves_retains_settles_and_counts(tmp_path):
    spending = Spending(tmp_path / "spending.json", "uncapped")
    spending.reserve("first", "episode", 20, "uncapped")
    spending.reserve("unknown", "episode", 30, "uncapped")
    spending.settle("first", 12)
    assert spending.retain("unknown") == 30
    affordable, context = spending.affordability(1000)
    assert affordable and context["campaign_committed_usd"] == 42
    assert context["campaign_cap_usd"] == "uncapped"
    assert context["campaign_headroom_usd"] is None
    assert context["unsettled_usd"] == 30
    json.dumps(PublicCostContext.model_validate(context).model_dump(), allow_nan=False)
    with pytest.raises(ValueError, match="DUPLICATE_RESERVATION"):
        spending.reserve("first", "episode", 1, "uncapped")


@pytest.mark.parametrize("episode,campaign,reason", [
    (1, "uncapped", "EPISODE_COST_CAP"),
    ("uncapped", 1, "CAMPAIGN_COST_CAP"),
])
def test_each_remaining_finite_cap_is_enforced(tmp_path, episode, campaign, reason):
    spending = Spending(tmp_path / "spending.json", campaign)
    with pytest.raises(BudgetExhausted, match=f"^{reason}$") as error:
        spending.reserve("request", "episode", 2, episode)
    PublicCostContext.model_validate(error.value.cost_context)
    assert not spending.path.exists()


@pytest.mark.parametrize("confirm", [False, None, "true", 1])
def test_uncapped_request_requires_strict_explicit_confirmation(confirm):
    with pytest.raises(ValidationError):
        RunInput(cost_override="uncapped", confirm_uncapped=confirm)


@pytest.mark.parametrize("override,confirmation", [(None, False), (10, False), ("uncapped", True)])
def test_run_override_is_local_and_never_enables_paid_execution(harness, monkeypatch, override, confirmation):
    h = harness
    h.config.budgets.paid_calls_enabled = False
    original = h.config.model_dump()
    app = create_app(h.store.root, h.config)
    received = []

    def start(config, *_args, **_kwargs):
        received.append(config)
        return "a" * 32

    monkeypatch.setattr(app.state.runs, "start", start)
    with TestClient(app) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        payload = {"agent": "luna", "offline": True}
        if override is not None:
            payload.update(cost_override=override, confirm_uncapped=confirmation)
        assert client.post("/api/runs", json=payload).status_code == 403
        response = client.post("/api/runs", headers=headers, json=payload)
        assert response.status_code == 200
    limits = received[0].budgets
    assert not limits.paid_calls_enabled
    assert limits.max_episode_cost_usd == (override if override is not None else h.config.budgets.max_episode_cost_usd)
    assert limits.max_batch_cost_usd == (override if override is not None else h.config.budgets.max_batch_cost_usd)
    assert app.state.config.model_dump() == original
    assert not app.state.settings_path.exists()
    assert not h.calls and not h.games


def test_unconfirmed_uncapped_post_creates_no_episode_or_provider(harness):
    h = harness
    with TestClient(create_app(h.store.root, h.config)) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        reply = client.post("/api/runs", headers=headers, json={"agent": "luna", "cost_override": "uncapped"})
        assert reply.status_code == 422
    assert h.store.list_episodes() == [] and not h.calls and not h.games


def test_confirmed_uncapped_still_requires_paid_enablement(harness):
    h = harness
    h.config.budgets.paid_calls_enabled = False
    with TestClient(create_app(h.store.root, h.config)) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        reply = client.post("/api/runs", headers=headers, json={
            "agent": "luna", "offline": True, "cost_override": "uncapped", "confirm_uncapped": True,
        })
        assert reply.json()["error"] == "PAID_EXECUTION_NOT_AUTHORIZED"
    assert h.store.list_episodes() == [] and not h.calls and not h.games


def test_uncapped_does_not_remove_provider_call_limit_or_cost_accounting(harness):
    h = harness
    h.config.budgets.max_episode_cost_usd = h.config.budgets.max_batch_cost_usd = "uncapped"
    h.config.budgets.max_provider_calls = 1
    result = h.service().execute(h.config, "luna", "UNPAID_FIXTURE", offline=True)
    assert result["outcome"] == "BUDGET_EXHAUSTED"
    assert result["provider_calls"] == 1 and result["cost_usd"] > 0
    assert len(h.calls) == 1
    h.native.assert_not_called()


def test_dollar_cap_policy_is_in_full_source_fingerprint():
    sources = source_files()
    previous = fingerprint_sources(sources)
    sources["src/balatro_horizons/cost_limits.py"] += b"\n# fingerprint mutation\n"
    assert fingerprint_sources(sources) != previous
