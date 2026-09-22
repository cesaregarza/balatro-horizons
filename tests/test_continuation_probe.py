from types import SimpleNamespace

import pytest

from balatro_horizons.evidence import continuation_probe as probe
from balatro_horizons.game.contract import NativeFailure


class _Store:
    def __init__(self, root):
        self.root = root
        self.calls = []


def test_native_probe_preflights_before_journal_inspection(tmp_path, monkeypatch):
    store = _Store(tmp_path)
    checkpoint = {
        "game": {"kind": "native"},
        "continuation_hash": "start",
        "observation": {},
    }
    order = []

    def preflight():
        order.append("preflight")

    def events(_eid):
        order.append("events")
        return [{"type": "observation", "observation_id": 2}, {"type": "terminal"}]

    class Observation:
        @staticmethod
        def model_validate(value):
            order.append("observation")
            return value

    class Envelope:
        def __init__(self, **kwargs):
            self.action = kwargs["action"]

    monkeypatch.setattr(probe, "load_session", preflight)
    monkeypatch.setattr(probe, "read_checkpoint", lambda *_: checkpoint)
    monkeypatch.setattr(probe, "Observation", Observation)
    monkeypatch.setattr(probe, "ActionEnvelope", Envelope)
    monkeypatch.setattr(probe, "validate_action", lambda *_: order.append("validate"))
    monkeypatch.setattr(store, "events", events, raising=False)

    probe._probe_inputs(store, SimpleNamespace(), "episode", 2, object(), 3)

    assert order == ["preflight", "events", "observation", "validate"]


def test_unconfigured_native_preflight_launches_no_game(tmp_path, monkeypatch):
    store = _Store(tmp_path)
    checkpoint = {
        "game": {"kind": "native"},
        "continuation_hash": "start",
        "observation": {},
    }
    launches = []
    monkeypatch.setattr(probe, "read_checkpoint", lambda *_: checkpoint)

    def preflight():
        raise ValueError("WINDOWS_SESSION_NOT_CONFIGURED")

    monkeypatch.setattr(probe, "load_session", preflight)
    monkeypatch.setattr(probe, "_new_game", lambda *_: launches.append(True))

    with pytest.raises(ValueError, match="WINDOWS_SESSION_NOT_CONFIGURED"):
        probe._probe_inputs(store, SimpleNamespace(), "episode", 2, object(), 3)

    assert launches == []


def test_native_probe_rechecks_identity_before_each_fresh_comparison(tmp_path, monkeypatch):
    store = _Store(tmp_path)
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    order = []
    games = []

    class Game:
        def close(self):
            order.append("close")

    def preflight():
        order.append("preflight")

    def new_game(*_args):
        order.append("new_game")
        game = Game()
        games.append(game)
        return game

    monkeypatch.setattr(probe, "load_session", preflight)
    monkeypatch.setattr(probe, "_new_game", new_game)
    monkeypatch.setattr(probe, "_probe_game", lambda *_args: "same-action-result")

    failures, after_hashes = probe._run_repetitions(
        store,
        SimpleNamespace(),
        "episode",
        2,
        {"game": {"kind": "native"}},
        SimpleNamespace(),
        True,
        3,
    )

    assert failures == []
    assert after_hashes == ["same-action-result"] * 3
    assert len(games) == 3
    assert order == [
        "preflight", "new_game", "close",
        "preflight", "new_game", "close",
        "preflight", "new_game", "close",
    ]


def test_expiry_at_second_repetition_preserves_pointer_and_records(tmp_path, monkeypatch):
    store = _Store(tmp_path)
    private = tmp_path / "private_runs" / "episode"
    private.mkdir(parents=True)
    selected = private / "continuation-probe-2.json"
    selected.write_text('{"status":"passed"}\n')
    before = selected.read_bytes()
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    checkpoint = {"game": {"kind": "native"}}
    monkeypatch.setattr(probe, "_probe_inputs", lambda *_args: (checkpoint, object(), True))
    order = []

    def preflight():
        order.append("preflight")
        if order.count("preflight") == 2:
            raise ValueError("WINDOWS_SESSION_EXPIRED")

    class Game:
        def close(self):
            order.append("close")

    def new_game(*_args):
        order.append("new_game")
        return Game()

    monkeypatch.setattr(probe, "load_session", preflight)
    monkeypatch.setattr(probe, "_new_game", new_game)
    monkeypatch.setattr(probe, "_probe_game", lambda *_args: "same-action-result")

    with pytest.raises(ValueError, match="WINDOWS_SESSION_EXPIRED"):
        probe.verify_continuation_probe(store, SimpleNamespace(), "episode", 2, object())

    assert order == ["preflight", "new_game", "close", "preflight"]
    assert selected.read_bytes() == before
    assert not list(private.glob("certificate-record-*.json"))


def test_operational_probe_failure_preserves_selected_pointer(tmp_path, monkeypatch):
    store = _Store(tmp_path)
    private = tmp_path / "private_runs" / "episode"
    private.mkdir(parents=True)
    selected = private / "continuation-probe-2.json"
    selected.write_text('{"status":"passed"}\n')
    before = selected.read_bytes()
    checkpoint = {"game": {"kind": "synthetic"}}
    monkeypatch.setattr(probe, "_probe_inputs", lambda *_args: (checkpoint, object(), False))
    monkeypatch.setattr(probe, "implementation_fingerprint", lambda: "a" * 64)

    def fail(*_args):
        raise NativeFailure("NATIVE_SESSION_EXPIRED")

    monkeypatch.setattr(probe, "_run_repetitions", fail)

    with pytest.raises(NativeFailure, match="NATIVE_SESSION_EXPIRED"):
        probe.verify_continuation_probe(store, SimpleNamespace(), "episode", 2, object())

    assert selected.read_bytes() == before
    assert not list(private.glob("certificate-record-*.json"))
