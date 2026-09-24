"""Run-level Restore uses the existing checked replay, with only synthetic doubles."""

from unittest.mock import Mock

import pytest
import test_campaign_budget
from recovery_support import replay_run
from test_restore_unfinished import request_for

from balatro_horizons import service_restore
from balatro_horizons.evidence import certification
from balatro_horizons.game.contract import NativeFailure
from balatro_horizons.storage.journal import atomic_json

harness = test_campaign_budget.harness
replay_run = replay_run


@pytest.fixture
def restore_replay(replay_run, monkeypatch):
    f = replay_run
    monkeypatch.setattr(service_restore, "load_session", Mock())
    return f


@pytest.mark.parametrize("fault,reason", [
    (None, "VERIFIED_ENGINE_TERMINAL"),
    ("target", "PRIVATE_CONTINUATION_DIVERGENCE"),
    ("public", "PUBLIC_REPLAY_DIVERGENCE"),
    ("private", "PRIVATE_CONTINUATION_DIVERGENCE"),
    ("unknown", "ACTION_STATUS_UNKNOWN"),
])
def test_restore_reaches_latest_boundary_before_any_provider(restore_replay, monkeypatch, fault, reason):
    f = restore_replay
    forbidden = Mock(side_effect=AssertionError("no preliminary certification"))
    monkeypatch.setattr(certification, "verify_checkpoint", forbidden)
    f.fault = fault
    service = f.h.service()
    preview = service_restore.restore_preview(service, f.eid, paid_enabled=True)
    assert preview["available"] and preview["plan"]["decision"] == 2
    assert not f.order and not f.h.calls
    service_restore.load_session.assert_not_called()
    child = service_restore.start_restore(service, f.eid, request_for(preview["plan"]), paid_enabled=True)
    service.thread.join(10)
    assert not service.thread.is_alive()
    assert f.h.store.summary(child)["reason"] == reason
    assert len(f.games) == 2 and f.order.count("create") == f.order.count("restore") == f.order.count("close") == 1
    if fault:
        assert not f.h.calls and "provider" not in f.order and "new-action" not in f.order
        assert f.order.count("replay-action") == (2 if fault == "target" else 1)
    else:
        assert f.order[:5] == ["create", "restore", "replay-action", "replay-action", "restored"]
        assert f.order.index("restored") < f.order.index("provider") < f.order.index("new-action")
    forbidden.assert_not_called()
    service_restore.load_session.assert_called_once()


def test_restore_still_requires_current_native_release(restore_replay):
    f = restore_replay
    f.gate.side_effect = NativeFailure("NATIVE_CAPABILITY_CERTIFICATION_REQUIRED")
    preview = service_restore.restore_preview(f.h.service(), f.eid, paid_enabled=True)
    assert not preview["available"] and preview["reason"] == "NATIVE_CAPABILITY_CERTIFICATION_REQUIRED"
    assert len(f.games) == 1 and not f.h.calls


@pytest.mark.parametrize("change,reason", [
    ("continuation_hash", "CHECKPOINT_CONTINUATION_MISMATCH"),
    ("observation", "CHECKPOINT_PREFIX_MISMATCH"),
    ("environment", "CHECKPOINT_ENVIRONMENT_MISMATCH"),
])
def test_restore_refuses_corrupt_saved_state_before_creation(restore_replay, change, reason):
    f = restore_replay
    checkpoint = certification.read_checkpoint(f.h.store, f.eid, 2)
    if change == "continuation_hash":
        checkpoint[change] = "f" * 64
    elif change == "observation":
        checkpoint[change]["phase"] = "SHOP"
    else:
        checkpoint["game"][change] = {}
    atomic_json(f.h.store.episode_path(f.eid, True) / "checkpoint-2.json", checkpoint)
    preview = service_restore.restore_preview(f.h.service(), f.eid, paid_enabled=True)
    assert not preview["available"] and preview["reason"] == reason
    assert len(f.h.store.list_episodes()) == len(f.games) == 1 and not f.h.calls
