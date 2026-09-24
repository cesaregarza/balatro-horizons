"""Funding, provenance and unsupported-lineage refusals never launch a child."""

import json

import pytest
import test_campaign_budget
from fastapi.testclient import TestClient
from test_restore_boundaries import unfinished
from test_restore_unfinished import interrupted, request_for

from balatro_horizons.api import create_app
from balatro_horizons.evidence.certification import read_checkpoint
from balatro_horizons.harness.terminals import INCOMPLETE_TERMINAL_REASON
from balatro_horizons.service_restore import restore_preview, start_restore
from balatro_horizons.storage.journal import atomic_json

harness = test_campaign_budget.harness


def test_unjournaled_reservation_is_not_refunded(harness):
    h = harness
    parent = interrupted(h)
    before = restore_preview(h.service(), parent, paid_enabled=True)["plan"]
    path = h.store.episode_path(parent, True) / "spending.json"
    ledger = json.loads(path.read_text())
    ledger["unknown-before-journal"] = {"episode_id": parent, "cost": 0.1, "settled": False}
    atomic_json(path, ledger)
    after = restore_preview(h.service(), parent, paid_enabled=True)["plan"]
    assert after["costs"]["accounted_usd"] == pytest.approx(before["costs"]["accounted_usd"] + 0.1)
    assert after["plan_hash"] != before["plan_hash"]
    assert len(h.games) == 1


@pytest.mark.parametrize("reason", [INCOMPLETE_TERMINAL_REASON, "PROCESS_INTERRUPTED"])
def test_incomplete_or_underfunded_terminal_is_not_treated_as_zero(harness, monkeypatch, reason):
    h = harness
    parent = unfinished(h, monkeypatch)
    h.store.finish(parent, {"outcome": "INFRASTRUCTURE_FAILURE", "reason": reason,
                           "provider_calls": len(h.calls), "cost_usd": 100})
    preview = restore_preview(h.service(), parent, paid_enabled=True)
    expected = reason if reason == INCOMPLETE_TERMINAL_REASON else "RESTORE_LEDGER_MISMATCH"
    assert not preview["available"] and preview["reason"] == expected
    assert len(h.store.list_episodes()) == 1


@pytest.mark.parametrize("kind,reason", [
    ("campaign", "RESTORE_CAMPAIGN_NOT_SUPPORTED"),
    ("intervention", "RESTORE_INTERVENTION_NOT_SUPPORTED"),
    ("fixture", "RECOVERY_FIXTURE_NOT_SUPPORTED"),
])
def test_unsupported_lineage_is_explicit(harness, kind, reason):
    h = harness
    parent = interrupted(h)
    manifest = h.store.manifest(parent)
    if kind == "campaign":
        manifest["batch_id"] = "a" * 32
    elif kind == "fixture":
        manifest["fixture"] = "excluded"
    else:
        manifest.update(parent_episode_id="b" * 32, assistance="intervention")
    atomic_json(h.store.episode_path(parent) / "manifest.json", manifest)
    preview = restore_preview(h.service(), parent, paid_enabled=True)
    assert not preview["available"] and preview["reason"] == reason


def test_current_disable_and_stale_ledger_refuse_post_without_child(harness):
    h = harness
    parent = interrupted(h)
    preview = restore_preview(h.service(), parent, paid_enabled=True)["plan"]
    with pytest.raises(ValueError, match="PAID_EXECUTION_NOT_AUTHORIZED"):
        start_restore(h.service(), parent, request_for(preview), paid_enabled=False)
    assert len(h.store.list_episodes()) == 1


def test_plan_is_bound_to_saved_checkpoint_as_well_as_public_head(harness):
    h = harness
    parent = interrupted(h)
    preview = restore_preview(h.service(), parent, paid_enabled=True)["plan"]
    checkpoint = read_checkpoint(h.store, parent, 0)
    checkpoint["memory"] = "synthetic change after preview"
    atomic_json(h.store.episode_path(parent, True) / "checkpoint-0.json", checkpoint)
    with pytest.raises(ValueError, match="RESTORE_PLAN_CHANGED"):
        start_restore(h.service(), parent, request_for(preview), paid_enabled=True)
    assert len(h.store.list_episodes()) == 1


def test_restore_post_does_not_accept_implicit_or_extended_authorization(harness):
    h = harness
    parent = interrupted(h)
    with TestClient(create_app(h.store.root, h.config, workbench_enabled=True)) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        path = f"/api/operator/episodes/{parent}/restore"
        plan = client.get(path, headers=headers).json()["plan"]
        body = {"parent_head": plan["parent_head"], "plan_hash": plan["plan_hash"]}
        assert client.post(path, headers=headers, json=body).json()["error"] == "PAID_EXECUTION_NOT_AUTHORIZED"
        for extra in ({"authorize_paid": "yes"}, {"combined_cap_usd": 100}, {"agent": "other"}):
            assert client.post(path, headers=headers, json={**body, **extra}).status_code == 422
    assert len(h.store.list_episodes()) == 1
