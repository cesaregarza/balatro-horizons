"""Budget extensions use synthetic games and mocked providers only."""

import json
from copy import deepcopy

import pytest
import test_campaign_budget
from fastapi.testclient import TestClient

from balatro_horizons.agents.baselines import Baseline
from balatro_horizons.agents.frozen import restore_protocol
from balatro_horizons.api import create_app
from balatro_horizons.engine.certification import read_checkpoint, require_checkpoint_certificate
from balatro_horizons.engine.continuation_probe import verify_continuation_probe
from balatro_horizons.engine.fake import FakeGame
from balatro_horizons.review.budget_continuation import prepare_budget_continuation
from balatro_horizons.storage.journal import atomic_json

harness = test_campaign_budget.harness


def stopped(h, *, helpers=False):
    h.config.budgets.max_episode_cost_usd = 0.017
    h.config.budgets.max_batch_cost_usd = 1
    if helpers:
        h.operations.append({"kind": "arithmetic", "expression": "1+1"})
    result = h.service().execute(h.config, "luna", "PRIVATE_FIXTURE", offline=True)
    h.operations.clear()
    assert result["outcome"] == "BUDGET_EXHAUSTED"
    eid = result["episode_id"]
    terminal = h.store.summary(eid)
    decision = max(e["observation_id"] for e in h.store.events(eid) if e["type"] == "observation")
    checkpoint = read_checkpoint(h.store, eid, decision)
    action = Baseline("heuristic").decide({"observation": checkpoint["observation"]}, [])["envelope"]["action"]
    return eid, terminal, decision, action


def certify(h, eid, decision, action):
    cert = verify_continuation_probe(h.store, h.config, eid, decision, action)
    assert cert["status"] == "passed"
    assert cert["completed_repetitions"] == 3
    assert len(set(cert["after_hashes"])) == 1
    assert cert["scope"] == "original_state_and_generated_same_action_probe"
    return cert


def test_extension_restores_game_and_charges_post_checkpoint_helpers(harness):
    h = harness
    eid, terminal, decision, action = stopped(h, helpers=True)
    before = (h.store.episode_path(eid) / "events.jsonl").read_bytes()
    checkpoint = read_checkpoint(h.store, eid, decision)
    original = restore_protocol(h.store, checkpoint)
    assert terminal["cost_usd"] > checkpoint["cost"]
    assert terminal["provider_calls"] > checkpoint["calls"]
    certify(h, eid, decision, action)
    plan = prepare_budget_continuation(h.store, eid, 1, expected_head=terminal["journal_head"])
    assert plan["resume"]["cost"] == pytest.approx(terminal["cost_usd"])
    assert plan["resume"]["calls"] == terminal["provider_calls"]
    expected = deepcopy(original)
    expected["episode_limits"]["max_episode_cost_usd"] = 1
    derived = restore_protocol(h.store, plan["resume"])
    derived.pop("budget_extension")
    assert derived == expected  # Prompts, tools, models and non-money limits unchanged.
    service = h.service()
    child = service.continue_budget(eid, 1, expected_head=terminal["journal_head"])
    service.thread.join(10)
    assert not service.thread.is_alive()
    assert service.error is None
    summary = h.store.summary(child)
    assert summary["outcome"] == "WIN"
    manifest = h.store.manifest(child)
    assert manifest["assistance"] == "budget_extension" and not manifest["evaluation_eligible"]
    assert manifest["parent_decision"] == decision
    ledger = json.loads((h.store.episode_path(eid, True) / "spending.json").read_text())
    assert sum(e["cost"] for e in ledger.values()) == pytest.approx(
        summary["cost_usd"] + terminal["cost_usd"])
    assert summary["provider_calls"] == len(ledger)
    assert (h.store.episode_path(eid) / "events.jsonl").read_bytes() == before
    assert read_checkpoint(h.store, eid, decision) == checkpoint
    assert restore_protocol(h.store, checkpoint) == original
    assert not any(e["type"] == "action_commit" for e in h.store.events(eid))


def test_extension_rejects_uncertified_changed_or_underfunded_parent(harness):
    h = harness
    eid, terminal, decision, action = stopped(h)
    service = h.service()
    count = len(h.store.list_episodes())
    with pytest.raises(ValueError, match="CHECKPOINT_NOT_CERTIFIED"):
        service.continue_budget(eid, 1, expected_head=terminal["journal_head"])
    certify(h, eid, decision, action)
    for cap, head, error in [
        (0.017, terminal["journal_head"], "MUST_INCREASE_CAP"),
        (1, "0" * 64, "PARENT_CHANGED"),
        (float("inf"), terminal["journal_head"], "NOT_AUTHORIZED"),
        (True, terminal["journal_head"], "NOT_AUTHORIZED"),
    ]:
        with pytest.raises(ValueError, match=error):
            service.continue_budget(eid, cap, expected_head=head)
    assert len(h.store.list_episodes()) == count


def test_failed_attempt_costs_and_unsettled_reservations_cannot_be_erased(harness):
    h = harness
    eid, terminal, decision, action = stopped(h)
    certify(h, eid, decision, action)
    plan = prepare_budget_continuation(h.store, eid, 1, expected_head=terminal["journal_head"])
    spending = plan["spending"]
    spending.reserve("failed-attempt", "failed-child", 0.1, 1)
    with pytest.raises(ValueError, match="UNSETTLED_SPENDING"):
        prepare_budget_continuation(h.store, eid, 1, expected_head=terminal["journal_head"])
    spending.settle("failed-attempt", 0.03)
    retry = prepare_budget_continuation(h.store, eid, 1, expected_head=terminal["journal_head"])
    assert retry["resume"]["cost"] == pytest.approx(terminal["cost_usd"] + 0.03)
    assert retry["resume"]["calls"] == terminal["provider_calls"] + 1


def test_probe_fails_on_first_divergence_without_relaunch_loop(harness, monkeypatch):
    h = harness
    eid, _, decision, action = stopped(h)
    certify(h, eid, decision, action)
    games = []

    class Divergent(FakeGame):
        def restore(self, snapshot):
            super().restore(snapshot)
            self.money += 1
            games.append(self)

    monkeypatch.setattr("balatro_horizons.engine.continuation_probe.FakeGame", Divergent)
    failed = verify_continuation_probe(h.store, h.config, eid, decision, action)
    assert failed["status"] == "failed"
    assert len(games) == 1 and failed["completed_repetitions"] == 0
    assert failed["failures"][0]["reason"] == "PRIVATE_CONTINUATION_DIVERGENCE"
    with pytest.raises(ValueError, match="CERTIFICATE_INVALID"):
        require_checkpoint_certificate(h.store, eid, decision)


def test_probe_checks_the_result_of_the_action_too(harness, monkeypatch):
    h = harness
    eid, _, decision, action = stopped(h)
    games = []

    class Divergent(FakeGame):
        def apply_public_action(self, *args):
            super().apply_public_action(*args)
            self.money += len(games)
            games.append(self)

    monkeypatch.setattr("balatro_horizons.engine.continuation_probe.FakeGame", Divergent)
    failed = verify_continuation_probe(h.store, h.config, eid, decision, action)
    assert failed["status"] == "failed" and len(games) == 2
    assert failed["failures"][0]["reason"] == "PROBE_CONTINUATION_DIVERGENCE"


def test_new_source_requires_explicit_intervention_not_ordinary_restore(harness, monkeypatch):
    h = harness
    eid, terminal, decision, action = stopped(h)
    checkpoint = read_checkpoint(h.store, eid, decision)
    old = restore_protocol(h.store, checkpoint)
    for module in ("agents.frozen", "review.budget_continuation", "engine.continuation_probe",
                   "engine.certification"):
        monkeypatch.setattr(f"balatro_horizons.{module}.implementation_fingerprint", lambda: "f" * 64)
    with pytest.raises(ValueError, match="IMPLEMENTATION_CHANGED"):
        restore_protocol(h.store, checkpoint)
    certify(h, eid, decision, action)
    plan = prepare_budget_continuation(h.store, eid, 1, expected_head=terminal["journal_head"])
    new = restore_protocol(h.store, plan["resume"])
    assert new["implementation_hash"] == "f" * 64
    assert new["budget_extension"]["recorded_implementation_hash"] == old["implementation_hash"]
    assert new["prompt_utf8"] == old["prompt_utf8"]


def test_operator_token_and_finite_explicit_cap_required(store, config):
    with TestClient(create_app(store.root, config)) as client:
        route = "/api/operator/episodes/unknown/continue-budget"
        body = {"combined_cap_usd": 10, "parent_terminal_hash": "a" * 64}
        assert client.post(route, json=body).status_code == 403
        token = client.get("/api/bootstrap").json()["operator_token"]
        headers = {"X-BH-Operator": token}
        for invalid in (True, "10", 0, -1):
            assert client.post(route, json={**body, "combined_cap_usd": invalid},
                               headers=headers).status_code == 422


def test_ledger_and_terminal_must_agree(harness):
    h = harness
    eid, terminal, decision, action = stopped(h)
    certify(h, eid, decision, action)
    path = h.store.episode_path(eid, True) / "spending.json"
    ledger = json.loads(path.read_text())
    next(iter(ledger.values()))["cost"] += 0.1
    atomic_json(path, ledger)
    with pytest.raises(ValueError, match="LEDGER_MISMATCH"):
        prepare_budget_continuation(h.store, eid, 1, expected_head=terminal["journal_head"])


def test_launch_plan_is_read_only_and_counts_only_targeted_restarts(harness):
    import importlib.util

    from balatro_horizons.config import ROOT

    h = harness
    eid, _, _, _ = stopped(h)
    module_spec = importlib.util.spec_from_file_location("continue_budget", ROOT / "scripts/continue_budget.py")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    before = (h.store.episode_path(eid) / "events.jsonl").read_bytes()
    plan = module.continuation_plan(h.store, eid)
    assert plan["launches"] == {"restoration_verification": 3, "paid_continuation": 1}
    assert plan["original_spent_usd"] == pytest.approx(plan["all_attempts_committed_usd"])
    assert plan["unsettled_usd"] == 0
    assert "PRIVATE_FIXTURE" not in json.dumps(plan)
    assert (h.store.episode_path(eid) / "events.jsonl").read_bytes() == before
