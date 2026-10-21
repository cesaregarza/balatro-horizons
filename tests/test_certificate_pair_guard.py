"""The capability certificate admits only the deck/stake pairs it covers.

These tests use a real certificate file and the real certificate check; only the
Windows session, the native lock and the game itself are synthetic.
"""

from unittest.mock import Mock

import pytest
import test_campaign_budget
from fastapi.testclient import TestClient
from recovery_support import replay_run

from balatro_horizons.api import create_app
from balatro_horizons.config import Config
from balatro_horizons.evidence import certification, lock, recovery
from balatro_horizons.evidence.provenance import implementation_fingerprint
from balatro_horizons.game import windows_context
from balatro_horizons.game.contract import NativeFailure
from balatro_horizons.run_configuration import require_native_selection
from balatro_horizons.storage.journal import atomic_json, digest

harness = test_campaign_budget.harness
__all__ = ["harness", "replay_run"]

LOCK = {"fixture": "native-shaped-synthetic-only"}
CERTIFIED = ("RED", "GOLD")
UNCERTIFIED = ("BLUE", "WHITE")


@pytest.fixture
def certified_root(tmp_path, monkeypatch):
    """Install a passed certificate covering only RED/GOLD for LOCK."""
    root = tmp_path / "certified-root"
    atomic_json(root / "private/capability-certificate.json", {
        "status": "passed",
        "environment_hash": digest(LOCK),
        "implementation_hash": implementation_fingerprint(),
        "configurations": [list(CERTIFIED)],
    })
    monkeypatch.setattr(certification, "ROOT", root)
    return root


def environment(pair):
    config = Config()
    config.environment.deck, config.environment.stake = pair
    return config.environment


def test_certificate_refuses_an_uncovered_pair_directly(certified_root):
    assert certification.require_environment_certificate(LOCK, environment(CERTIFIED))
    with pytest.raises(NativeFailure) as refused:
        certification.require_environment_certificate(LOCK, environment(UNCERTIFIED))
    assert refused.value.code == "NATIVE_CAPABILITY_CERTIFICATE_MISMATCH"


def test_native_selection_refuses_an_uncovered_pair(certified_root, monkeypatch):
    monkeypatch.setattr(windows_context, "load_session", Mock())
    monkeypatch.setattr(lock, "read_lock", lambda: LOCK)
    require_native_selection(environment(CERTIFIED))
    with pytest.raises(ValueError, match="^NATIVE_CAPABILITY_CERTIFICATE_MISMATCH$"):
        require_native_selection(environment(UNCERTIFIED))


def test_native_launch_refuses_an_uncovered_pair_before_any_episode(
    store, certified_root, monkeypatch
):
    monkeypatch.setattr(windows_context, "load_session", Mock())
    monkeypatch.setattr(lock, "read_lock", lambda: LOCK)
    app = create_app(store.root, Config())
    start = Mock(return_value="c" * 32)
    app.state.runs.start = start
    with TestClient(app) as client:
        headers = {"X-BH-Operator": client.get("/api/bootstrap").json()["operator_token"]}
        deck, stake = UNCERTIFIED
        refused = client.post("/api/runs", headers=headers, json={
            "deck": deck, "stake": stake, "offline": False,
        })
        assert refused.status_code == 400
        assert "NATIVE_CAPABILITY_CERTIFICATE_MISMATCH" in refused.text
        start.assert_not_called()
        assert store.list_episodes() == []
        deck, stake = CERTIFIED
        admitted = client.post("/api/runs", headers=headers, json={
            "deck": deck, "stake": stake, "offline": False,
        })
    assert admitted.status_code == 200, admitted.text
    start.assert_called_once()


def test_recovery_refuses_an_uncovered_pair(replay_run, certified_root, monkeypatch):
    f = replay_run
    # The fixture stubs the certificate check; restore the real one for this test.
    monkeypatch.setattr(
        recovery, "require_environment_certificate", certification.require_environment_certificate
    )
    episodes = len(f.h.store.list_episodes())
    config = f.h.config.model_copy(deep=True)
    config.environment.deck, config.environment.stake = CERTIFIED
    recovery.recovery_checkpoint(f.h.store, config, f.eid, 2)
    config.environment.deck, config.environment.stake = UNCERTIFIED
    with pytest.raises(ValueError, match="^NATIVE_CAPABILITY_CERTIFICATE_MISMATCH$"):
        recovery.recovery_checkpoint(f.h.store, config, f.eid, 2)
    assert len(f.h.store.list_episodes()) == episodes
    assert not f.order and not f.h.calls
