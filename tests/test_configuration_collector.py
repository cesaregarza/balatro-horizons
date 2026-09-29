"""Synthetic control-flow tests; these are not native configuration evidence."""

import fcntl
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from balatro_horizons.cli import main
from balatro_horizons.config import Config
from balatro_horizons.evidence.collect import configurations as collector
from balatro_horizons.evidence.stages import plan
from balatro_horizons.storage.journal import digest


@pytest.fixture
def harness(tmp_path, monkeypatch):
    root, baseline = tmp_path / "candidate", tmp_path / "baseline"
    for path in (root, baseline):
        (path / "private").mkdir(parents=True)
    lock, parent = {"environment": "test"}, {"certificate": "parent"}
    certificate = {"accepted_implementation_hash": "source", "native_implementation_hash": "native",
                   "environment_hash": digest(lock), "reuse": {"parent_certificate_id": "parent",
                   "parent_certificate_hash": digest(parent)}}
    monkeypatch.setattr(collector, "ROOT", root)
    monkeypatch.setattr(collector, "load_session", Mock())
    monkeypatch.setattr(collector, "implementation_fingerprint", lambda: "source")
    monkeypatch.setattr(collector, "catalog_hash", lambda _: "catalog")
    monkeypatch.setattr(collector, "lock_digest", lambda _: digest(lock))
    monkeypatch.setattr(collector.reuse, "prepare", Mock(return_value=(certificate, {})))
    monkeypatch.setattr(collector.reuse, "_read_certificate", lambda _: (parent, "parent"))
    games = []
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.bridge.verify_files.return_value = lock

    def new_game(environment, seed):
        game = Mock()
        game.lock = lock
        game.inspect_raw.return_value = {"deck": environment.deck, "stake": environment.stake,
                                        "state": "BLIND_SELECT", "bh": {"ready": True, "busy": False, "profile": {}}}
        game.observe_private.return_value = {"visible": {"phase": "BLIND_SELECT", "available_action_types": ["select_blind"]}}
        games.append(game)
        return game

    session.new_game.side_effect = new_game
    factory = Mock(return_value=session)
    return SimpleNamespace(root=root, baseline=baseline, report=tmp_path / "report.json",
                           factory=factory, session=session, games=games)


def run(harness):
    return collector.collect(Config(), harness.baseline, harness.root / "offline.json",
                             harness.report, session_factory=harness.factory)


def test_plan_is_one_owned_process_without_paid_calls_or_implicit_publication(capsys):
    assert main(["evidence", "plan", "--configurations-only"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["expected_physical_launches"] == 1 and result["expected_game_resets"] == 120
    assert not result["release_certification_requested"] and not result["capability_activation_requested"]
    with pytest.raises(ValueError, match="MUTUALLY_EXCLUSIVE"):
        plan(configurations_only=True, gameplay_only=True)


def test_all_pairs_share_one_session_and_every_lease_is_closed(harness):
    result = run(harness)
    assert result["status"] == "passed"
    assert result["configurations_checked"] == result["game_resets"] == 120
    assert result["native_launches"] == 1 and result["paid_calls"] == 0
    harness.factory.assert_called_once()
    harness.session.__exit__.assert_called_once()
    for game in harness.games:
        game.close.assert_called_once()
    report = json.loads(harness.report.read_text())
    assert {(row["deck"], row["stake"]) for row in report["cases"]} == set(collector.pairs())
    assert not report["restoration_certified"]
    assert not (harness.root / "private/capability-certificate.json").exists()
    assert "seed" not in harness.report.read_text().lower()


def test_wrong_observed_configuration_stops_without_retry_or_relaunch(harness):
    create = harness.session.new_game.side_effect

    def wrong(environment, seed):
        game = create(environment, seed)
        if len(harness.games) == 2:
            game.inspect_raw.return_value["deck"] = "UNEXPECTED"
        return game

    harness.session.new_game.side_effect = wrong
    result = run(harness)
    assert result["status"] == "failed" and result["reason"] == "NATIVE_CONFIGURATION_MISMATCH"
    assert result["configurations_checked"] == 1 and result["game_resets"] == 2
    harness.factory.assert_called_once()
    for game in harness.games:
        game.close.assert_called_once()
    harness.session.__exit__.assert_called_once()


@pytest.mark.parametrize("worker", ["baseline", "root"])
def test_busy_worker_is_never_interrupted(harness, worker):
    with (getattr(harness, worker) / "private/native-worker.lock").open("a") as active:
        fcntl.flock(active, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = run(harness)
    assert result["status"] == "failed" and result["reason"] == "NATIVE_WORKER_BUSY"
    assert result["native_launches"] == result["game_resets"] == 0
    harness.factory.assert_not_called()
    collector.load_session.assert_not_called()


@pytest.mark.parametrize("error_type", [KeyError, ValueError, OSError])
def test_malformed_private_state_keeps_a_sanitized_failed_receipt(harness, error_type):
    create = harness.session.new_game.side_effect

    def malformed(environment, seed):
        game = create(environment, seed)
        game.inspect_raw.side_effect = error_type("PRIVATE_SENTINEL")
        return game

    harness.session.new_game.side_effect = malformed
    result = run(harness)
    assert result["status"] == "failed" and result["reason"] == error_type.__name__
    assert "PRIVATE_SENTINEL" not in harness.report.read_text()
    harness.games[0].close.assert_called_once()
    harness.session.__exit__.assert_called_once()


def test_existing_report_is_never_replaced(harness):
    harness.report.write_text("retain")
    with pytest.raises(ValueError, match="REPORT_ALREADY_EXISTS"):
        run(harness)
    assert harness.report.read_text() == "retain"
    harness.factory.assert_not_called()


def test_collection_refuses_source_change_and_preserves_failed_receipt(harness, monkeypatch):
    monkeypatch.setattr(collector, "implementation_fingerprint", Mock(side_effect=["source", "changed"]))
    result = run(harness)
    assert result["status"] == "failed" and result["reason"] == "SOURCE_CHANGED_DURING_COLLECTION"
    assert harness.report.exists()
    harness.session.__exit__.assert_called_once()


def test_incompatible_cli_modes_are_refused_before_collection():
    with pytest.raises(ValueError, match="CONFIGURATION_COLLECTION_REQUIRES"):
        from balatro_horizons.cli.evidence import run_evidence_command
        from balatro_horizons.cli.parser import build_parser

        run_evidence_command(build_parser().parse_args([
            "evidence", "collect", "--configurations-only", "--gameplay-only",
        ]))
