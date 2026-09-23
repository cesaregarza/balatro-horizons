"""Budget extensions use synthetic games and mocked providers only."""

import json
from copy import deepcopy

import pytest
import test_campaign_budget
from fastapi.testclient import TestClient

from balatro_horizons import service as service_module
from balatro_horizons.api import create_app
from balatro_horizons.evaluation.reports import episode_export
from balatro_horizons.evidence.certification import (
    certificate_path,
    continuation_probe_path,
    read_checkpoint,
    require_checkpoint_certificate,
)
from balatro_horizons.evidence.continuation_probe import verify_continuation_probe
from balatro_horizons.harness.baselines import Baseline
from balatro_horizons.harness.context.freeze import read_protocol, restore_protocol
from balatro_horizons.harness.money import reservation_usd
from balatro_horizons.review.decision_ledger import build_summary
from balatro_horizons.review.export import export_response
from balatro_horizons.storage.journal import atomic_json
from balatro_horizons.workbench.budget_continuation import prepare_budget_continuation

harness = test_campaign_budget.harness
STOP_CAP_USD = 0.019


def stopped(h, *, helpers=False):
    h.config.budgets.max_episode_cost_usd = STOP_CAP_USD
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
    plan = prepare_budget_continuation(h.store, eid, 2, expected_head=terminal["journal_head"])
    assert plan["resume"]["cost"] == pytest.approx(terminal["cost_usd"])
    assert plan["resume"]["calls"] == terminal["provider_calls"]
    expected = deepcopy(original)
    expected["episode_limits"]["max_episode_cost_usd"] = 2
    derived = restore_protocol(h.store, plan["resume"])
    derived.pop("budget_extension")
    assert derived == expected  # Prompts, tools, models and non-money limits unchanged.
    service = h.service()
    child = service.continue_budget(eid, 2, expected_head=terminal["journal_head"])
    service.thread.join(10)
    assert not service.thread.is_alive()
    assert service.error is None
    summary = h.store.summary(child)
    assert summary["outcome"] == "WIN"
    manifest = h.store.manifest(child)
    assert manifest["assistance"] == "budget_extension" and not manifest["evaluation_eligible"]
    assert manifest["parent_decision"] == decision
    exported = episode_export(h.store, child)["manifest"]
    assert exported["recovery"] == manifest["recovery"]
    assert "certificate_id" not in exported
    assert exported["budget_extension"] == manifest["budget_extension"]
    assert exported["budget_extension"]["root_batch_cap_usd"] == 1
    assert exported["budget_extension"]["previous_cap_usd"] == STOP_CAP_USD
    assert exported["budget_extension"]["combined_cap_usd"] == 2
    ledger = json.loads((h.store.episode_path(eid, True) / "spending.json").read_text())
    assert sum(e["cost"] for e in ledger.values()) == pytest.approx(
        summary["cost_usd"] + terminal["cost_usd"])
    assert summary["provider_calls"] == len(ledger)
    assert (h.store.episode_path(eid) / "events.jsonl").read_bytes() == before
    assert read_checkpoint(h.store, eid, decision) == checkpoint
    assert restore_protocol(h.store, checkpoint) == original
    assert not any(e["type"] == "action_commit" for e in h.store.events(eid))


@pytest.mark.parametrize("format", ["json", "jsonl"])
def test_budget_extension_review_export_preserves_provenance(harness, format):
    h = harness
    eid, terminal, decision, action = stopped(h, helpers=True)
    certify(h, eid, decision, action)
    service = h.service()
    child = service.continue_budget(eid, 2, expected_head=terminal["journal_head"])
    service.thread.join(10)
    assert not service.thread.is_alive() and service.error is None
    manifest = h.store.manifest(child)
    content = export_response(build_summary(h.store, child), format).body.decode()
    rows = [json.loads(content)] if format == "json" else [json.loads(row) for row in content.splitlines()]
    assert rows
    for row in rows:
        assert row["episode"]["recovery"] == manifest["recovery"]
        assert row["episode"]["budget_extension"] == manifest["budget_extension"]
        assert row["episode"]["budget_extension"]["root_batch_cap_usd"] == 1
        assert row["cost_accounting"] == "Runner provider calls are inherited-inclusive; cost is own-only."
    assert "PRIVATE_FIXTURE" not in content


def test_extension_rejects_changed_or_underfunded_parent_without_requiring_probe(harness):
    h = harness
    eid, terminal, decision, action = stopped(h)
    service = h.service()
    count = len(h.store.list_episodes())
    for cap, head, error in [
        (STOP_CAP_USD, terminal["journal_head"], "MUST_INCREASE_CAP"),
        (1, "0" * 64, "PARENT_CHANGED"),
        (float("inf"), terminal["journal_head"], "NOT_AUTHORIZED"),
        (True, terminal["journal_head"], "NOT_AUTHORIZED"),
    ]:
        with pytest.raises(ValueError, match=error):
            service.continue_budget(eid, cap, expected_head=head)
    assert len(h.store.list_episodes()) == count


def test_probe_certificate_cannot_authorize_an_ordinary_branch(harness):
    h = harness
    eid, _, decision, action = stopped(h)
    certify(h, eid, decision, action)
    with pytest.raises(ValueError, match="CHECKPOINT_NOT_CERTIFIED"):
        require_checkpoint_certificate(h.store, eid, decision)
    # A copied probe record still fails the mode/scope check at the ordinary path.
    probe = json.loads(continuation_probe_path(h.store, eid, decision).read_text())
    atomic_json(certificate_path(h.store, eid, decision), probe)
    with pytest.raises(ValueError, match="CHECKPOINT_CERTIFICATE_INVALID"):
        require_checkpoint_certificate(h.store, eid, decision)


def test_unowned_spending_rows_are_not_admitted_even_if_settled(harness):
    h = harness
    eid, terminal, decision, action = stopped(h)
    certify(h, eid, decision, action)
    plan = prepare_budget_continuation(h.store, eid, 1, expected_head=terminal["journal_head"])
    spending = plan["spending"]
    spending.reserve("failed-attempt", "failed-child", 0.1, 1)
    with pytest.raises(ValueError, match="LEDGER_MISMATCH"):
        prepare_budget_continuation(h.store, eid, 1, expected_head=terminal["journal_head"])
    spending.settle("failed-attempt", 0.03)
    with pytest.raises(ValueError, match="LEDGER_MISMATCH"):
        prepare_budget_continuation(h.store, eid, 1, expected_head=terminal["journal_head"])


def test_failed_child_terminal_reconciles_three_retained_reservations(harness):
    h = harness
    eid, terminal, decision, action = stopped(h)
    certify(h, eid, decision, action)
    h.failures.append(True)
    service = h.service()
    child_id = service.continue_budget(eid, 1, expected_head=terminal["journal_head"])
    service.thread.join(10)
    assert not service.thread.is_alive() and service.error is None
    child = h.store.summary(child_id)
    assert child["outcome"] == "INFRASTRUCTURE_FAILURE"
    path = h.store.episode_path(eid, True) / "spending.json"
    ledger = json.loads(path.read_text())
    retained = {key: row for key, row in ledger.items() if row["episode_id"] == child_id}
    assert len(retained) == 3 and all(not row["settled"] for row in retained.values())
    expected_retained = 3 * reservation_usd(h.config.models["luna"], h.config.budgets)
    assert sum(row["cost"] for row in retained.values()) == pytest.approx(expected_retained)
    assert child["cost_usd"] == pytest.approx(expected_retained)
    assert child["provider_calls"] == terminal["provider_calls"] + 3

    # The same locked snapshot supplies both terminal reconciliation and admission.
    retry = prepare_budget_continuation(h.store, eid, 1, expected_head=terminal["journal_head"])
    assert retry["resume"]["cost"] == pytest.approx(terminal["cost_usd"] + expected_retained)
    assert retry["resume"]["calls"] == terminal["provider_calls"] + 3
    h.failures.clear()
    second = h.service()
    second_id = second.continue_budget(eid, 1, expected_head=terminal["journal_head"])
    second.thread.join(10)
    assert not second.thread.is_alive() and second.error is None
    assert h.store.summary(second_id)["outcome"] == "WIN"
    assert h.store.manifest(second_id)["budget_extension"]["prior_cost_usd"] == pytest.approx(
        terminal["cost_usd"] + expected_retained
    )
    # Index maintenance must not change the immutable admission chain.
    h.store.reindex(eid)
    after_reindex = prepare_budget_continuation(h.store, eid, 1, expected_head=terminal["journal_head"])
    assert after_reindex["resume"]["calls"] == h.store.summary(second_id)["provider_calls"]
    ledger = json.loads(path.read_text())
    del ledger[next(iter(retained))]
    atomic_json(path, ledger)
    with pytest.raises(ValueError, match="CHILD_SPEND_MISMATCH"):
        prepare_budget_continuation(h.store, eid, 1, expected_head=terminal["journal_head"])


def test_pre_run_child_failure_with_no_new_calls_allows_reconciliation(harness, monkeypatch):
    from unittest.mock import Mock

    h = harness
    eid, terminal, decision, action = stopped(h)
    certify(h, eid, decision, action)
    game = Mock(side_effect=RuntimeError("simulated pre-run failure"))
    monkeypatch.setattr(service_module, "FakeGame", game)
    service = h.service()
    child_id = service.continue_budget(eid, 1, expected_head=terminal["journal_head"])
    service.thread.join(10)
    assert not service.thread.is_alive()
    child = h.store.summary(child_id)
    assert child["outcome"] == "INFRASTRUCTURE_FAILURE"
    assert child["provider_calls"] == 0 and child["cost_usd"] == 0
    game.assert_called_once()
    retry = prepare_budget_continuation(h.store, eid, 1, expected_head=terminal["journal_head"])
    assert retry["resume"]["calls"] == terminal["provider_calls"]
    monkeypatch.setattr(service_module, "FakeGame", type(h.games[0]))
    second = h.service()
    second_id = second.continue_budget(eid, 1, expected_head=terminal["journal_head"])
    second.thread.join(10)
    assert not second.thread.is_alive() and second.error is None
    assert h.store.summary(second_id)["outcome"] == "WIN"
    for child in (child_id, second_id):
        h.store.reindex(child)
        after_reindex = prepare_budget_continuation(
            h.store, eid, 1, expected_head=terminal["journal_head"]
        )
        assert after_reindex["resume"]["calls"] == h.store.summary(second_id)["provider_calls"]


def test_root_retained_reservation_is_counted_and_can_be_extended(harness):
    h = harness
    h.failures.append(True)
    eid, terminal, decision, action = stopped(h)
    rows = json.loads((h.store.episode_path(eid, True) / "spending.json").read_text())
    assert len(rows) == terminal["provider_calls"]
    assert any(not row["settled"] for row in rows.values())
    assert sum(row["cost"] for row in rows.values()) == pytest.approx(terminal["cost_usd"])
    certify(h, eid, decision, action)
    plan = prepare_budget_continuation(h.store, eid, 1, expected_head=terminal["journal_head"])
    assert plan["resume"]["cost"] == pytest.approx(terminal["cost_usd"])
    assert plan["resume"]["calls"] == terminal["provider_calls"]


def test_recovered_child_terminal_uses_child_only_call_count(harness):
    h = harness
    eid, terminal, decision, action = stopped(h)
    certify(h, eid, decision, action)
    plan = prepare_budget_continuation(h.store, eid, 1, expected_head=terminal["journal_head"])
    child_id = h.store.create(plan["manifest"], plan["private"])
    h.store.append(child_id, "episode_start", {"evidence_kind": "SYNTHETIC_TEST"})
    request_id = "f" * 32
    amount = 0.02
    plan["spending"].reserve(request_id, child_id, amount, 1,
                             prior_cost=plan["resume"]["cost"])
    h.store.append(child_id, "provider_request", {"reserved_usd": amount},
                   request_id=request_id)
    assert h.store.recover() == [child_id]
    child = h.store.summary(child_id)
    assert child["provider_calls"] == 1 and child["cost_usd"] == amount
    retry = prepare_budget_continuation(h.store, eid, 1, expected_head=terminal["journal_head"])
    assert retry["resume"]["calls"] == terminal["provider_calls"] + 1
    assert retry["resume"]["cost"] == pytest.approx(terminal["cost_usd"] + amount)


def test_two_service_processes_cannot_admit_siblings_from_same_ledger(harness):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from unittest.mock import Mock

    h = harness
    eid, terminal, decision, action = stopped(h)
    certify(h, eid, decision, action)
    services = [h.service(), h.service()]
    for service in services:
        service._launch = Mock()  # Hold the admitted child unresolved without a worker.
    start = Barrier(3)

    def admit(service):
        start.wait()
        try:
            return service.continue_budget(eid, 1, expected_head=terminal["journal_head"])
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(admit, service) for service in services]
        start.wait()
        results = [future.result() for future in pending]
    assert results.count("BUDGET_EXTENSION_CHILD_UNRESOLVED") == 1
    assert len([result for result in results if len(result) == 32]) == 1
    children = [row for row in h.store.list_episodes()
                if row["manifest"].get("parent_episode_id") == eid]
    assert len(children) == 1


def test_money_intervention_cannot_bypass_a_changed_frozen_protocol(harness, monkeypatch):
    h = harness
    eid, terminal, decision, action = stopped(h)
    checkpoint = read_checkpoint(h.store, eid, decision)
    old = restore_protocol(h.store, checkpoint)
    for module in ("harness.context.freeze", "workbench.budget_continuation", "evidence.continuation_probe",
                   "evidence.certification"):
        monkeypatch.setattr(f"balatro_horizons.{module}.implementation_fingerprint", lambda: "f" * 64)
    with pytest.raises(ValueError, match="IMPLEMENTATION_CHANGED"):
        restore_protocol(h.store, checkpoint)
    certify(h, eid, decision, action)
    with pytest.raises(ValueError, match="IMPLEMENTATION_CHANGED"):
        prepare_budget_continuation(h.store, eid, 1, expected_head=terminal["journal_head"])
    assert read_protocol(h.store, checkpoint) == old


def test_operator_token_and_finite_explicit_cap_required(store, config):
    config.workbench_enabled = True
    with TestClient(create_app(store.root, config)) as client:
        route = "/api/operator/episodes/" + "f" * 32 + "/continue-budget"
        body = {"combined_cap_usd": 10, "parent_terminal_hash": "a" * 64}
        assert client.post(route, json=body).status_code == 403
        token = client.get("/api/bootstrap").json()["operator_token"]
        headers = {"X-BH-Operator": token}
        for invalid in (True, "10", 0, -1):
            assert client.post(route, json={**body, "combined_cap_usd": invalid},
                               headers=headers).status_code == 422
        assert client.post(route, json=body, headers=headers).status_code == 404


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
    import balatro_horizons.cli.continue_budget as module

    h = harness
    eid, _, _, _ = stopped(h)
    before = (h.store.episode_path(eid) / "events.jsonl").read_bytes()
    plan = module.continuation_plan(h.store, eid)
    assert plan["launches"] == {"restoration_verification": 0, "paid_continuation": 1}
    assert plan["original_spent_usd"] == pytest.approx(plan["all_attempts_committed_usd"])
    assert plan["unsettled_usd"] == 0
    assert "PRIVATE_FIXTURE" not in json.dumps(plan)
    assert (h.store.episode_path(eid) / "events.jsonl").read_bytes() == before
