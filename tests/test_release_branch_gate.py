"""Release bootstrap stays certificate-backed and separate from routine recovery."""

from unittest.mock import Mock

import pytest
import test_campaign_budget
from recovery_support import replay_run

from balatro_horizons.evidence.certification import verify_checkpoint
from balatro_horizons.evidence.release import _branch_restoration
from balatro_horizons.service import RunService
from balatro_horizons.workbench import branches

harness = test_campaign_budget.harness
replay_run = replay_run


def test_release_branch_bootstraps_without_production_admission(store, episode, config, monkeypatch):
    cert = verify_checkpoint(store, config, episode, 0)
    production_gate = Mock(side_effect=AssertionError("release cannot require its own future pass"))
    monkeypatch.setattr(branches, "recovery_checkpoint", production_gate)
    modes = []
    original = RunService.create_game

    def record_mode(self, *args, **kwargs):
        modes.append(kwargs["calibration"])
        return original(self, *args, **kwargs)

    monkeypatch.setattr(RunService, "create_game", record_mode)
    before = (store.episode_path(episode) / "events.jsonl").read_bytes()
    child, summary, _ = _branch_restoration(store, config, episode)
    assert summary["outcome"] == "WIN" and modes == [True]
    manifest = store.manifest(child)
    assert manifest["certificate_id"] == cert["certificate_id"]
    assert manifest["validation_purpose"] == "release_branch"
    assert "recovery" not in manifest and not manifest["evaluation_eligible"]
    assert (store.episode_path(episode) / "events.jsonl").read_bytes() == before
    production_gate.assert_not_called()


def test_release_branch_still_requires_a_replay_certificate(store, episode, config):
    before = len(store.list_episodes())
    with pytest.raises(ValueError, match="CHECKPOINT_NOT_CERTIFIED"):
        _branch_restoration(store, config, episode)
    assert len(store.list_episodes()) == before


@pytest.mark.parametrize("mode", ["single_action_override", "human_takeover"])
def test_release_calibration_cannot_admit_a_paid_or_human_branch(replay_run, mode):
    f = replay_run
    before = len(f.h.store.list_episodes())
    with pytest.raises(ValueError, match="CALIBRATION_REQUIRES_SCRIPTED_POLICY"):
        f.h.service().branch(f.h.config, f.eid, 0, mode, actions=[{}], calibration=True)
    assert len(f.h.store.list_episodes()) == before
    assert not f.order and not f.h.calls
