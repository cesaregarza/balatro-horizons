"""Browser funding contract against fake games and mock-only provider calls."""

import json

import pytest
import test_campaign_budget
from fastapi.testclient import TestClient
from test_budget_continuation import stopped
from test_restore_unfinished import finish, interrupted, request_for

from balatro_horizons.api import create_app
from balatro_horizons.evidence.certification import read_checkpoint
from balatro_horizons.service_restore import restore_preview, start_restore
from balatro_horizons.workbench import budget_continuation
from balatro_horizons.workbench.budget_continuation import budget_preview

harness = test_campaign_budget.harness


def test_preview_is_read_only_and_adds_ten_to_accounted_spend(harness):
    h = harness
    parent, terminal, _, _ = stopped(h)
    roots = (h.store.episode_path(parent), h.store.episode_path(parent, True))
    before = {p: p.read_bytes() for root in roots for p in root.rglob("*") if p.is_file()}
    rows = h.store.list_episodes()
    calls = len(h.calls)
    response = budget_preview(h.service(), parent, paid_enabled=True)
    assert response["available"] and response["reason"] is None
    plan = response["plan"]
    assert plan["accounted_usd"] == pytest.approx(terminal["cost_usd"])
    assert plan["new_cap_usd"] == plan["accounted_usd"] + 10
    assert plan["additional_usd"] == 10
    assert plan["additional_available"] and plan["additional_reason"] is None
    assert plan["source_compatibility"] == "same_source"
    assert len(h.calls) == calls and len(h.games) == 1
    assert {p: p.read_bytes() for root in roots for p in root.rglob("*") if p.is_file()} == before
    assert h.store.list_episodes() == rows


def test_additional_ten_route_creates_immutable_funded_child(harness):
    h = harness
    parent, _, _, _ = stopped(h)
    originals = {p: p.read_bytes() for p in h.store.episode_path(parent).rglob("*") if p.is_file()}
    app = create_app(h.store.root, h.config, workbench_enabled=True)
    with TestClient(app) as client:
        url = f"/api/operator/episodes/{parent}/continue-budget"
        assert client.get(url).status_code == 403
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        plan = client.get(url, headers=headers).json()["plan"]
        payload = {"parent_terminal_hash": plan["parent_terminal_hash"],
                   "plan_hash": plan["plan_hash"], "additional_cost_usd": 10, "authorize_paid": True}
        reply = client.post(url, headers=headers, json=payload)
        assert reply.status_code == 200, reply.json()
        child = reply.json()["episode_id"]
        finish(app.state.runs)
    extension = h.store.manifest(child)["budget_extension"]
    assert extension["combined_cap_usd"] == plan["accounted_usd"] + 10
    assert extension["prior_cost_usd"] == plan["accounted_usd"]
    assert h.store.summary(child)["outcome"] == "WIN"
    assert h.store.manifest(child)["evaluation_eligible"] is False
    assert {p: p.read_bytes() for p in originals} == originals
    h.native.assert_not_called()


def test_uncapped_confirmation_is_required_before_any_continuation(harness):
    h = harness
    parent, _, _, _ = stopped(h)
    app = create_app(h.store.root, h.config, workbench_enabled=True)
    with TestClient(app) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        url = f"/api/operator/episodes/{parent}/continue-budget"
        plan = client.get(url, headers=headers).json()["plan"]
        payload = {"parent_terminal_hash": plan["parent_terminal_hash"],
                   "plan_hash": plan["plan_hash"], "combined_cap_usd": "uncapped", "authorize_paid": True}
        calls = len(h.calls)
        assert client.post(url, headers=headers, json=payload).status_code == 422
        assert len(h.store.list_episodes()) == 1 and len(h.calls) == calls
        payload["confirm_uncapped"] = True
        reply = client.post(url, headers=headers, json=payload)
        assert reply.status_code == 200, reply.json()
        child = reply.json()["episode_id"]
        finish(app.state.runs)
    assert h.store.manifest(child)["budget_extension"]["combined_cap_usd"] == "uncapped"
    assert h.store.summary(child)["outcome"] == "WIN"
    assert h.store.summary(child)["cost_usd"] > 0


def test_failed_attempt_spend_invalidates_offer_and_is_in_next_increment(harness):
    h = harness
    parent, terminal, _, _ = stopped(h)
    service = h.service()
    previous = budget_preview(service, parent, paid_enabled=True)["plan"]
    h.failures.append(True)
    child = service.continue_budget(parent, 1, expected_head=terminal["journal_head"])
    finish(service)
    h.failures.clear()
    assert h.store.summary(child)["outcome"] == "INFRASTRUCTURE_FAILURE"
    count, calls = len(h.store.list_episodes()), len(h.calls)
    with pytest.raises(ValueError, match="BUDGET_PLAN_CHANGED"):
        service.continue_budget(parent, None, expected_head=previous["parent_terminal_hash"],
                                additional_cost=10, expected_plan_hash=previous["plan_hash"])
    assert len(h.store.list_episodes()) == count and len(h.calls) == calls
    current = budget_preview(service, parent, paid_enabled=True)["plan"]
    ledger = json.loads((h.store.episode_path(parent, True) / "spending.json").read_text())
    assert sum(row["cost"] for row in ledger.values() if not row["settled"]) > 0
    accounted = sum(row["cost"] for row in ledger.values())
    assert current["accounted_usd"] == accounted
    assert current["new_cap_usd"] == accounted + 10
    assert current["accounted_usd"] > previous["accounted_usd"]
    assert current["new_cap_usd"] == current["accounted_usd"] + 10


def test_preview_and_post_preserve_global_paid_gate_and_read_only_mode(harness):
    h = harness
    parent, terminal, _, _ = stopped(h)
    assert budget_preview(h.service(), parent, paid_enabled=False)["reason"] == "PAID_EXECUTION_NOT_AUTHORIZED"
    h.config.budgets.paid_calls_enabled = False
    app = create_app(h.store.root, h.config, workbench_enabled=True)
    url = f"/api/operator/episodes/{parent}/continue-budget"
    with TestClient(app) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        reply = client.post(url, headers=headers, json={"combined_cap_usd": 10,
                            "parent_terminal_hash": terminal["journal_head"], "authorize_paid": True})
        assert reply.json()["error"] == "PAID_EXECUTION_NOT_AUTHORIZED"
    with TestClient(create_app(h.store.root, h.config, workbench_enabled=False)) as readonly:
        assert readonly.get(url).status_code == 404


def test_restoring_uncapped_run_keeps_tracking_and_requires_reconfirmation(harness):
    h = harness
    h.config.budgets.max_episode_cost_usd = h.config.budgets.max_batch_cost_usd = "uncapped"
    parent = interrupted(h)
    service = h.service()
    plan = restore_preview(service, parent, paid_enabled=True)["plan"]
    assert plan["limits"] == {"max_episode_cost_usd": "uncapped", "max_batch_cost_usd": "uncapped"}
    assert plan["costs"]["remaining_episode_usd"] is None
    assert plan["costs"]["remaining_batch_usd"] is None
    assert plan["costs"]["accounted_usd"] > 0
    with pytest.raises(ValueError, match="UNCAPPED_CONFIRMATION_REQUIRED"):
        start_restore(service, parent, request_for(plan), paid_enabled=True)
    child = start_restore(service, parent, request_for(plan, confirm_uncapped=True), paid_enabled=True)
    finish(service)
    assert h.store.summary(child)["outcome"] == "WIN"
    assert read_checkpoint(h.store, child, 0)["cost"] >= plan["costs"]["accounted_usd"]


@pytest.mark.parametrize("episode_cap", [100, "uncapped"])
def test_add_ten_after_campaign_only_stop_uses_the_binding_cap(harness, episode_cap):
    h = harness
    h.config.budgets.max_episode_cost_usd = episode_cap
    h.config.budgets.max_batch_cost_usd = 0.019
    result = h.service().execute(h.config, "luna", "PRIVATE_FIXTURE", offline=True)
    assert result["reason"] == "CAMPAIGN_COST_CAP"
    parent = result["episode_id"]
    service = h.service()
    plan = budget_preview(service, parent, paid_enabled=True)["plan"]
    assert plan["new_cap_usd"] == plan["accounted_usd"] + 10
    child = service.continue_budget(parent, None, expected_head=plan["parent_terminal_hash"],
                                    additional_cost=10, expected_plan_hash=plan["plan_hash"])
    finish(service)
    assert h.store.summary(child)["outcome"] == "WIN"
    assert h.store.manifest(child)["budget_extension"]["previous_cap_usd"] == episode_cap


@pytest.mark.parametrize("offset", [-0.001, 0.001])
def test_additional_admission_independently_rechecks_offer_accounting(harness, monkeypatch, offset):
    h = harness
    parent, _, _, _ = stopped(h)
    service = h.service()
    offer = budget_continuation.budget_offer(h.store, parent)
    # A self-consistent but under/over-counted offer must not fund a child.
    mutated = {**offer, "accounted_usd": offer["accounted_usd"] + offset,
               "new_cap_usd": offer["accounted_usd"] + offset + 10}
    monkeypatch.setattr(budget_continuation, "budget_offer", lambda *_: mutated)
    calls, games = len(h.calls), len(h.games)
    with pytest.raises(ValueError, match="BUDGET_INCREMENT_MISMATCH"):
        service.continue_budget(parent, None, expected_head=offer["parent_terminal_hash"],
                                additional_cost=10, expected_plan_hash=offer["plan_hash"])
    assert len(h.store.list_episodes()) == 1 and (len(h.calls), len(h.games)) == (calls, games)


@pytest.mark.parametrize("increment", [True, 9, 11, "10", 10.0])
def test_service_rejects_invalid_increment_before_launch(harness, increment):
    h = harness
    parent, _, _, _ = stopped(h)
    offer = budget_continuation.budget_offer(h.store, parent)
    calls, games = len(h.calls), len(h.games)
    with pytest.raises(ValueError, match="INVALID_BUDGET_INCREMENT"):
        h.service().continue_budget(parent, None, expected_head=offer["parent_terminal_hash"],
                                    additional_cost=increment, expected_plan_hash=offer["plan_hash"])
    assert len(h.store.list_episodes()) == 1 and (len(h.calls), len(h.games)) == (calls, games)


def test_preview_disables_ten_if_next_reservation_exceeds_allowance(harness, monkeypatch):
    h = harness
    parent, _, _, _ = stopped(h)
    service = h.service()
    monkeypatch.setattr(service, "validate_policy", lambda *_: 11)
    result = budget_preview(service, parent, paid_enabled=True)
    assert result["available"], result
    offer = result["plan"]
    assert not offer["additional_available"]
    assert offer["additional_reason"] == "BUDGET_EXTENSION_BELOW_RESERVATION"
    with pytest.raises(ValueError, match="BUDGET_EXTENSION_BELOW_RESERVATION"):
        service.continue_budget(parent, None, expected_head=offer["parent_terminal_hash"],
                                additional_cost=10, expected_plan_hash=offer["plan_hash"])
    assert len(h.store.list_episodes()) == 1


def test_empty_child_journal_is_named_refusal_not_index_error(harness):
    h = harness
    parent, _, _, _ = stopped(h)
    child = h.store.create({"parent_episode_id": parent, "assistance": "agent_continue"}, {})
    assert h.store.events(child) == []
    response = budget_preview(h.service(), parent, paid_enabled=True)
    assert response == {"episode_id": parent, "available": False,
                        "reason": "BUDGET_EXTENSION_CHILD_UNRESOLVED", "plan": None}


def test_preview_disables_ten_when_it_would_not_increase_original_binding_cap(harness):
    h = harness
    # Synthetic pricing gives a ~$61 reservation and $1.50 actual mock calls:
    # a $65 ceiling stops early, with accounted + $10 still below that ceiling.
    h.config.models["luna"].output_usd_per_million = 7500.0
    h.config.budgets.max_episode_cost_usd = h.config.budgets.max_batch_cost_usd = 65.0
    result = h.service().execute(h.config, "luna", "FIXTURE", offline=True)
    assert result["outcome"] == "BUDGET_EXHAUSTED"
    preview = budget_preview(h.service(), result["episode_id"], paid_enabled=True)
    assert preview["available"], preview
    assert preview["plan"]["new_cap_usd"] < 65
    assert not preview["plan"]["additional_available"]
    assert preview["plan"]["additional_reason"] == "BUDGET_EXTENSION_MUST_INCREASE_CAP"
    h.native.assert_not_called()
