"""Native-shaped synthetic replay fixture; never launches Balatro or a real provider."""

from functools import partial
from shutil import copyfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from balatro_horizons import service as service_module
from balatro_horizons import service_restore
from balatro_horizons.evidence import recovery
from balatro_horizons.game.fake import FakeGame
from balatro_horizons.game.replay import restore_seed_prefix
from balatro_horizons.game.session import NativeFailure
from balatro_horizons.storage.journal import atomic_json, digest


class ReplayGame(FakeGame):
    evidence_kind = "NATIVE"  # Exercise native routing, not native game evidence.

    def __init__(self, environment, seed, *, trace, calibration=False):
        assert calibration is False, "Recovery must retain release/profile gating"
        super().__init__(seed)
        self.trace, self.lock, self.restoring = trace, trace.lock, False
        trace.games.append(self)
        trace.order.append("create")

    def checkpoint(self):
        return {"kind": "native", "environment": self.lock, "seed": self.private_seed}

    def restore(self, snapshot):
        self.trace.order.append("restore")
        self.restoring = True
        restore_seed_prefix(self, snapshot)
        self.restoring = False
        if self.trace.fault == "target":
            self.money += 1
        self.trace.order.append("restored")

    def apply_public_action(self, *args):
        trace = self.trace
        trace.order.append("replay-action" if self.restoring else "new-action")
        super().apply_public_action(*args)
        if self.restoring and trace.fault == "unknown":
            raise NativeFailure("ACTION_STATUS_UNKNOWN")
        if self.restoring and trace.fault == "public":
            self.money += 1
        if self.restoring and trace.fault == "private":
            self.private_seed += "-changed"
        if len(trace.games) == 1 and self.committed == 2:
            trace.h.operations.append({"kind": "abort", "reason": "fixture boundary"})

    def close(self):
        self.trace.order.append("close")


def provider_with_order(provider, order):
    def counted_provider(*args):
        policy = provider(*args)
        send = policy.send

        def counted_send(body):
            order.append("provider")
            return send(body)

        policy.send = counted_send
        return policy

    return counted_provider


@pytest.fixture
def replay_run(harness, monkeypatch, tmp_path):
    h = harness
    trace = SimpleNamespace(h=h, games=[], order=[], fault=None,
                            lock={"fixture": "native-shaped-synthetic-only"},
                            gate=Mock(return_value={"status": "passed"}))
    # Native-shaped doubles must not consume a checkout's real calibration rules.
    trace.root = tmp_path / "replay-root"
    prompts = trace.root / "configs/prompts"
    prompts.mkdir(parents=True)
    for name in ("harness.txt", "ALWAYS-LOADED.md"):
        copyfile(service_module.ROOT / "configs/prompts" / name, prompts / name)
    atomic_json(trace.root / "private/rules.json",
                {"environment_hash": digest(trace.lock), "core": "Synthetic replay fixture."})
    monkeypatch.setattr(service_module, "ROOT", trace.root)
    monkeypatch.setattr(service_restore, "ROOT", trace.root)
    monkeypatch.setattr(service_module, "NativeGame", partial(ReplayGame, trace=trace))
    monkeypatch.setattr(service_module, "load_session", lambda: None)
    monkeypatch.setattr(service_module, "DirectProvider",
                        provider_with_order(service_module.DirectProvider, trace.order))
    monkeypatch.setattr(recovery, "read_lock", lambda *_: trace.lock)
    monkeypatch.setattr(recovery, "require_environment_certificate", trace.gate)
    parent = h.service().execute(h.config, "luna", "PRIVATE_RECOVERY_FIXTURE")
    assert parent["reason"] == "AGENT_ABORT" and parent["committed_actions"] == 2
    h.operations.clear()
    h.calls.clear()
    trace.order.clear()
    trace.eid = parent["episode_id"]
    return trace
