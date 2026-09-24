"""Restore admission and all-attempt accounting; synthetic games/mock providers only."""

import json
from copy import deepcopy

import pytest
import test_campaign_budget
from fastapi.testclient import TestClient

from balatro_horizons.api import create_app
from balatro_horizons.api.models import RestoreInput
from balatro_horizons.evaluation.reports import episode_export
from balatro_horizons.evidence.certification import read_checkpoint
from balatro_horizons.review.decision_ledger import summary_input
from balatro_horizons.review.operator_status import OperatorStatus
from balatro_horizons.service_restore import restore_preview, start_restore
from balatro_horizons.storage.journal import atomic_json
from balatro_horizons.workbench.restoration import prepare_restore

harness = test_campaign_budget.harness


def interrupted(h):
    h.failures.append(True)
    result = h.service().execute(h.config, "luna", "PRIVATE_RESTORE_FIXTURE", offline=True)
    h.failures.clear()
    assert result["outcome"] == "INFRASTRUCTURE_FAILURE"
    assert result["reason"] == "PROVIDER_TRANSPORT_UNKNOWN"
    return result["episode_id"]


def request_for(plan, **overrides):
    return RestoreInput(parent_head=plan["parent_head"], plan_hash=plan["plan_hash"],
                        **{"authorize_paid": True, "accept_compatible_update": True, **overrides})


def finish(service):
    service.thread.join(10)
    assert not service.thread.is_alive()
    assert service.error is None


def test_restore_preview_is_read_only_and_preserves_unknown_spending(harness):
    h = harness
    parent = interrupted(h)
    before = {path: path.read_bytes() for path in h.store.root.rglob("*") if path.is_file()}
    games, calls = len(h.games), len(h.calls)
    preview = restore_preview(h.service(), parent, paid_enabled=True)
    assert preview["available"] and preview["reason"] is None
    plan = preview["plan"]
    assert plan["decision"] == 0 and plan["source_compatibility"] == "same_source"
    assert plan["launches"] == {"verification": 0, "continuation": 1}
    assert plan["costs"]["accounted_usd"] == h.store.summary(parent)["cost_usd"]
    assert len(h.games) == games and len(h.calls) == calls
    assert {path: path.read_bytes() for path in h.store.root.rglob("*") if path.is_file()} == before


def test_restore_uses_one_child_and_shared_budget_without_changing_parent(harness):
    h = harness
    parent = interrupted(h)
    checkpoint = read_checkpoint(h.store, parent, 0)
    before = (h.store.episode_path(parent) / "events.jsonl").read_bytes()
    preview = restore_preview(h.service(), parent, paid_enabled=True)
    service = h.service()
    child = start_restore(service, parent, request_for(preview["plan"]), paid_enabled=True)
    finish(service)
    assert len(h.games) == 2 and h.store.summary(child)["outcome"] == "WIN"
    assert (h.store.episode_path(parent) / "events.jsonl").read_bytes() == before
    assert read_checkpoint(h.store, parent, 0) == checkpoint
    manifest = h.store.manifest(child)
    assert manifest["assistance"] == "restoration" and not manifest["evaluation_eligible"]
    assert manifest["restoration"]["spending_owner_episode_id"] == parent
    assert manifest["restoration"]["prior_provider_calls"] == h.store.summary(parent)["provider_calls"]
    ledger = json.loads((h.store.episode_path(parent, True) / "spending.json").read_text())
    assert len(ledger) == h.store.summary(child)["provider_calls"]
    assert sum(row["cost"] for row in ledger.values()) == pytest.approx(
        h.store.summary(parent)["cost_usd"] + h.store.summary(child)["cost_usd"]
    )
    assert episode_export(h.store, child)["manifest"]["restoration"] == manifest["restoration"]
    retrospective = summary_input(h.store, child)["spend"]
    operator = next(row for row in OperatorStatus(h.store, service.review).episodes()
                    if row["episode_id"] == child)["spend"]
    assert retrospective == operator
    assert retrospective["combined_usd"] == pytest.approx(sum(row["cost"] for row in ledger.values()))
    assert retrospective["prior_usd"] == h.store.summary(parent)["cost_usd"]
    assert restore_preview(service, parent, paid_enabled=True)["reason"] == "RESTORE_ALREADY_FINISHED"


def test_failed_restore_attempt_is_charged_before_next_retry(harness):
    h = harness
    parent = interrupted(h)
    first = prepare_restore(h.store, parent, paid_enabled=True)["public"]
    h.failures.append(True)
    service = h.service()
    child = start_restore(service, parent, request_for(first), paid_enabled=True)
    finish(service)
    h.failures.clear()
    retry = prepare_restore(h.store, child, paid_enabled=True)
    assert retry["ledger"]["root"] == parent
    assert retry["resume"]["calls"] == h.store.summary(child)["provider_calls"]
    assert retry["resume"]["cost"] == pytest.approx(
        h.store.summary(parent)["cost_usd"] + h.store.summary(child)["cost_usd"]
    )
    with pytest.raises(ValueError, match="RESTORE_PLAN_CHANGED"):
        start_restore(h.service(), parent, request_for(first), paid_enabled=True)
    second = h.service()
    restored = start_restore(second, child, request_for(retry["public"]), paid_enabled=True)
    finish(second)
    assert h.store.summary(restored)["outcome"] == "WIN"


@pytest.mark.parametrize("change,error", [
    ({"authorize_paid": False}, "PAID_EXECUTION_NOT_AUTHORIZED"),
    ({"plan_hash": "a" * 64}, "RESTORE_PLAN_CHANGED"),
    ({"parent_head": "b" * 64}, "RESTORE_PLAN_CHANGED"),
])
def test_confirmation_refusals_create_no_child_or_provider(harness, change, error):
    h = harness
    parent = interrupted(h)
    plan = prepare_restore(h.store, parent, paid_enabled=True)["public"]
    request = request_for(plan).model_copy(update=change)
    before = len(h.calls)
    with pytest.raises(ValueError, match=error):
        start_restore(h.service(), parent, request, paid_enabled=True)
    assert len(h.store.list_episodes()) == 1 and len(h.calls) == before


def test_busy_disabled_and_finished_runs_are_not_restorable(harness):
    h = harness
    parent = interrupted(h)
    service = h.service()
    with service.admission():
        assert restore_preview(service, parent, paid_enabled=True)["reason"] == "WORKER_BUSY"
    assert restore_preview(service, parent, paid_enabled=False)["reason"] == "PAID_EXECUTION_NOT_AUTHORIZED"
    result = h.service().execute(h.config, "luna", "PRIVATE_DONE", offline=True)
    assert result["outcome"] == "WIN"
    assert restore_preview(service, result["episode_id"], paid_enabled=True)["reason"] == "RESTORE_RUN_FINISHED"


def test_missing_ledger_reservation_and_exhausted_caps_refuse(harness):
    h = harness
    parent = interrupted(h)
    path = h.store.episode_path(parent, True) / "spending.json"
    ledger = json.loads(path.read_text())
    damaged = deepcopy(ledger)
    del damaged[next(iter(damaged))]
    atomic_json(path, damaged)
    with pytest.raises(ValueError, match="RESTORE_LEDGER_MISMATCH"):
        prepare_restore(h.store, parent, paid_enabled=True)
    atomic_json(path, ledger)
    h.config.budgets.max_episode_cost_usd = 0.04
    h.failures.append(True)
    result = h.service().execute(h.config, "luna", "PRIVATE_CAP_FIXTURE", offline=True)
    h.failures.clear()
    assert result["outcome"] == "BUDGET_EXHAUSTED"
    second = result["episode_id"]
    assert restore_preview(h.service(), second, paid_enabled=True)["reason"] == "EPISODE_COST_CAP"


def test_restore_route_requires_operator_and_workbench_and_explicit_post(harness):
    h = harness
    parent = interrupted(h)
    app = create_app(h.store.root, h.config, workbench_enabled=True)
    with TestClient(app) as client:
        path = f"/api/operator/episodes/{parent}/restore"
        assert client.get(path).status_code == 403
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        preview = client.get(path, headers=headers)
        assert preview.status_code == 200 and preview.json()["available"]
        assert len(h.store.list_episodes()) == 1
        request = request_for(preview.json()["plan"])
        assert client.post(path, json=request.model_dump()).status_code == 403
        result = client.post(path, headers=headers, json=request.model_dump())
        assert result.status_code == 200
        finish(app.state.runs)
    with TestClient(create_app(h.store.root, h.config, workbench_enabled=False)) as readonly:
        assert readonly.get(path).status_code == 404
