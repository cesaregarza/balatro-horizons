"""One inline replay before mocked provider execution; no native or paid calls."""

import json
from unittest.mock import Mock

import pytest
import test_campaign_budget
from fastapi.testclient import TestClient
from recovery_support import replay_run

from balatro_horizons.api import create_app
from balatro_horizons.config import Config
from balatro_horizons.evidence import certification, recovery
from balatro_horizons.game.session import NativeFailure
from balatro_horizons.storage.journal import atomic_json

harness = test_campaign_budget.harness
replay_run = replay_run


def run_child(fixture, *, parent=None, decision=2):
    service = fixture.h.service()
    child = service.branch(fixture.h.config, parent or fixture.eid, decision, "agent_continue")
    service.thread.join(10)
    assert not service.thread.is_alive()
    return child, service


def test_latest_boundary_replays_once_then_pays_in_same_instance(replay_run, monkeypatch):
    f = replay_run
    before = (f.h.store.episode_path(f.eid) / "events.jsonl").read_bytes()
    no_verify = Mock(side_effect=AssertionError("no preliminary verification"))
    monkeypatch.setattr(certification, "verify_checkpoint", no_verify)
    child, service = run_child(f)
    assert service.error is None and f.h.store.summary(child)["outcome"] == "WIN"
    assert len(f.games) == 2  # One original fixture, one restoring/continuing worker.
    assert f.order[:5] == ["create", "restore", "replay-action", "replay-action", "restored"]
    assert f.order.index("restored") < f.order.index("provider") < f.order.index("new-action")
    assert f.order.count("restore") == f.order.count("create") == f.order.count("close") == 1
    assert f.h.calls and f.gate.call_count == 1
    no_verify.assert_not_called()
    manifest = f.h.store.manifest(child)
    assert manifest["recovery"]["mode"] == "seed_prefix"
    assert "certificate_id" not in manifest and not manifest["evaluation_eligible"]
    assert (f.h.store.episode_path(f.eid) / "events.jsonl").read_bytes() == before
    assert not list(f.h.store.episode_path(f.eid, True).glob("certificate*.json"))


@pytest.mark.parametrize("fault,code", [
    ("target", "PRIVATE_CONTINUATION_DIVERGENCE"),
    ("public", "PUBLIC_REPLAY_DIVERGENCE"),
    ("private", "PRIVATE_CONTINUATION_DIVERGENCE"),
    ("unknown", "ACTION_STATUS_UNKNOWN"),
])
def test_failed_restore_closes_one_instance_without_a_provider_call(replay_run, fault, code):
    f = replay_run
    f.fault = fault
    child, service = run_child(f)
    assert service.error == code
    assert f.h.store.summary(child)["reason"] == code
    assert not f.h.calls and "provider" not in f.order and "new-action" not in f.order
    assert f.order.count("create") == f.order.count("restore") == f.order.count("close") == 1
    assert f.order.count("replay-action") == (2 if fault == "target" else 1)


@pytest.mark.parametrize("change,code", [
    ("hash", "CHECKPOINT_CONTINUATION_MISMATCH"),
    ("observation", "CHECKPOINT_PREFIX_MISMATCH"),
    ("source", "CHECKPOINT_IMPLEMENTATION_CHANGED"),
    ("environment", "CHECKPOINT_ENVIRONMENT_MISMATCH"),
])
def test_invalid_saved_state_is_refused_before_launch_or_child(replay_run, change, code):
    f = replay_run
    checkpoint = certification.read_checkpoint(f.h.store, f.eid, 2)
    if change == "hash":
        checkpoint.pop("continuation_hash")
    elif change == "observation":
        checkpoint["observation"]["phase"] = "SHOP"
    elif change == "source":
        checkpoint["implementation_hash"] = "0" * 64
    else:
        checkpoint["game"]["environment"] = {}
    atomic_json(f.h.store.episode_path(f.eid, True) / "checkpoint-2.json", checkpoint)
    before = len(f.h.store.list_episodes())
    with pytest.raises(ValueError, match=code):
        f.h.service().branch(f.h.config, f.eid, 2, "agent_continue")
    assert len(f.h.store.list_episodes()) == before
    assert len(f.games) == 1 and not f.order and not f.h.calls


def test_environment_certificate_still_required_without_a_checkpoint_certificate(replay_run):
    f = replay_run
    f.gate.side_effect = NativeFailure("NATIVE_CAPABILITY_CERTIFICATION_REQUIRED")
    with pytest.raises(ValueError, match="NATIVE_CAPABILITY_CERTIFICATION_REQUIRED"):
        f.h.service().branch(f.h.config, f.eid, 2, "agent_continue")
    assert len(f.games) == 1 and not f.h.calls


def test_ancestor_replay_excludes_discarded_future_and_supports_child_boundary(replay_run):
    f = replay_run
    child, _ = run_child(f, decision=1)
    f.order.clear()
    f.h.calls.clear()
    grandchild, service = run_child(f, parent=child, decision=3)
    assert service.error is None and f.h.store.summary(grandchild)["outcome"] == "WIN"
    assert f.order.count("restore") == f.order.count("create") == 1
    assert f.order.count("replay-action") == 3
    assert f.order.index("restored") < f.order.index("provider")
    assert len(f.games) == 3


def test_recovery_admission_is_read_only_and_not_a_native_pass(replay_run):
    f = replay_run
    checkpoint, receipt = recovery.recovery_checkpoint(f.h.store, f.h.config, f.eid, 2)
    assert len(checkpoint["game"]["steps"]) == 2
    assert receipt.keys() == {"policy", "mode", "checkpoint_hash"}
    assert receipt["policy"] == "single_restore_v1" and "passed" not in json.dumps(receipt)
    assert not f.order and not f.h.calls


def test_missing_native_release_disables_capability_without_an_http_500(replay_run):
    f = replay_run
    f.gate.side_effect = NativeFailure("NATIVE_CAPABILITY_CERTIFICATION_REQUIRED")
    with TestClient(create_app(f.h.store.root, Config(workbench_enabled=True))) as client:
        token = client.get("/api/bootstrap").json()["operator_token"]
        opened = client.post("/api/reviews", json={"episode_id": f.eid},
                             headers={"X-BH-Operator": token}).json()
        response = client.get("/api/review/branch-capability",
                              headers={"X-Review-Token": opened["review_token"]})
    assert response.status_code == 200 and response.json()["enabled"] is False
    assert not f.order and not f.h.calls


def test_missing_private_evidence_at_an_intermediate_step_is_refused(replay_run):
    f = replay_run
    (f.h.store.episode_path(f.eid, True) / "raw-1.json").unlink()
    with pytest.raises(ValueError, match="CHECKPOINT_CONTINUATION_MISMATCH"):
        recovery.recovery_checkpoint(f.h.store, f.h.config, f.eid, 2)
    assert not f.order and not f.h.calls


def test_evaluator_fixture_cannot_enter_production_recovery(replay_run):
    f = replay_run
    manifest = f.h.store.manifest(f.eid)
    manifest["fixture"] = "excluded-evaluator-case"
    atomic_json(f.h.store.episode_path(f.eid) / "manifest.json", manifest)
    with pytest.raises(ValueError, match="RECOVERY_FIXTURE_NOT_SUPPORTED"):
        recovery.recovery_checkpoint(f.h.store, f.h.config, f.eid, 2)
    assert not f.order and not f.h.calls
