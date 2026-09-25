"""Funding consent and bounded numbers are enforced before worker admission."""

from unittest.mock import Mock

import pytest
import test_campaign_budget
from fastapi.testclient import TestClient
from pydantic import ValidationError

from balatro_horizons.api import create_app
from balatro_horizons.api.models import BudgetContinuationInput

harness = test_campaign_budget.harness
HEAD = "a" * 64


@pytest.mark.parametrize("funding", [
    {"combined_cap_usd": 10}, {"additional_cost_usd": 10},
    {"combined_cap_usd": "uncapped", "confirm_uncapped": True},
])
@pytest.mark.parametrize("consent", [{}, {"authorize_paid": False}, {"authorize_paid": 1}, {"authorize_paid": "true"}])
def test_all_funding_requires_independent_strict_consent(harness, funding, consent):
    app = create_app(harness.store.root, harness.config, workbench_enabled=True)
    app.state.runs.continue_budget = Mock(return_value="b" * 32)
    with TestClient(app) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        reply = client.post("/api/operator/episodes/parent/continue-budget", headers=headers, json={
            "parent_terminal_hash": HEAD, "plan_hash": HEAD, **funding, **consent,
        })
        assert reply.status_code == 422
        if consent.get("authorize_paid") in (None, False):
            assert "BUDGET_PLAN_AUTHORIZATION_REQUIRED" in reply.text
    app.state.runs.continue_budget.assert_not_called()
    assert not harness.calls and not harness.games and not harness.store.list_episodes()


@pytest.mark.parametrize("fields,code", [
    ({}, "ONE_BUDGET_OVERRIDE_REQUIRED"),
    ({"combined_cap_usd": 1, "additional_cost_usd": 10}, "ONE_BUDGET_OVERRIDE_REQUIRED"),
    ({"additional_cost_usd": 10}, "BUDGET_PLAN_AUTHORIZATION_REQUIRED"),
    ({"combined_cap_usd": "uncapped", "confirm_uncapped": True}, "BUDGET_PLAN_AUTHORIZATION_REQUIRED"),
])
def test_named_funding_refusals(fields, code):
    with pytest.raises(ValidationError, match=code):
        BudgetContinuationInput(parent_terminal_hash=HEAD, authorize_paid=True, **fields)


@pytest.mark.parametrize("cap", [10**400, 1e300, 1_000_001, True, "10"])
def test_invalid_numeric_caps_are_client_errors_not_overflow_500(harness, cap):
    app = create_app(harness.store.root, harness.config, workbench_enabled=True)
    app.state.runs.continue_budget = Mock(return_value="b" * 32)
    with TestClient(app) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        reply = client.post("/api/operator/episodes/parent/continue-budget", headers=headers, json={
            "combined_cap_usd": cap, "parent_terminal_hash": HEAD, "authorize_paid": True,
        })
        assert reply.status_code == 422
    app.state.runs.continue_budget.assert_not_called()


def test_numeric_funding_normalizes_integers_without_requiring_preview():
    request = BudgetContinuationInput(combined_cap_usd=10, parent_terminal_hash=HEAD, authorize_paid=True)
    assert type(request.model_dump()["combined_cap_usd"]) is float
    assert request.plan_hash is None
