"""Offline control-flow contracts, not evidence of native fault classification."""

import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from balatro_horizons.cli import main
from balatro_horizons.config import Config
from balatro_horizons.evidence.collect import interruption as collector
from balatro_horizons.evidence.stages import plan
from balatro_horizons.game.contract import NativeFailure
from balatro_horizons.game.replay import ReplayDivergence


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setattr(collector, "ROOT", tmp_path)
    monkeypatch.setattr(collector, "load_session", Mock())
    monkeypatch.setattr(collector, "source_identity", Mock(return_value={"commit": "test"}))
    bridge = Mock()
    bridge.verify_files.return_value = {"manifest": "pinned"}
    monkeypatch.setattr(collector, "WindowsBridge", Mock(return_value=bridge))
    monkeypatch.setattr(collector, "require_source_instrumentation", Mock())
    monkeypatch.setattr(collector, "require_idle", Mock(return_value={"owned_processes": 0}))
    store = Mock()
    store.events.return_value = [{"hash": "parent"}]
    fixture = (store, "episode", {"seed": "PRIVATE_SEED"}, [])
    monkeypatch.setattr(collector, "_capture", Mock(return_value=fixture))
    monkeypatch.setattr(collector, "_replay", Mock())
    return tmp_path / "receipt.json", fixture


def test_plan_has_seven_isolated_launches_and_does_not_certify(capsys):
    assert main(["evidence", "plan", "--interruption-only"]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["expected_physical_launches"] == receipt["expected_game_resets"] == 7
    assert not receipt["native_evidence_collected"]
    assert not receipt["release_certification_requested"]
    assert not receipt["capability_activation_requested"]
    for mode in ("connection_only", "continuation_only", "gameplay_only", "resume_actions", "resume_certification"):
        with pytest.raises(ValueError, match="MUTUALLY_EXCLUSIVE"):
            plan(interruption_only=True, **{mode: True})


def test_attempt_order_and_receipt_scope_are_explicit(harness):
    report, _ = harness
    result = collector.collect(Config(), report)
    assert result["status"] == "passed"
    assert result["launch_attempts"] == 7
    assert [row["scenario"] for row in result["attempts"]] == [
        "fixture_capture", "baseline_1", "baseline_2", "baseline_3", "divergence", "hang", "transport_loss",
    ]
    assert all(row["status"] == "observed" for row in result["attempts"])
    assert result["parent_journal_unchanged"]
    assert not result["complete_issue42_acceptance"] and not result["actual_socket_expiry_tested"]
    assert not result["certificate_selection_changed"] and not result["restoration_certification_included"]
    assert result["provider_calls"] == result["cost_usd"] == 0
    assert json.loads(report.read_text()) == result
    assert report.stat().st_mode & 0o777 == 0o600
    assert "PRIVATE_SEED" not in report.read_text()


@pytest.mark.parametrize("failure_at", range(6))
def test_first_unexpected_failure_stops_collection_without_retry(harness, failure_at):
    report, _ = harness
    collector._replay.side_effect = [None] * failure_at + [NativeFailure("UNEXPECTED_LOSS")]
    result = collector.collect(Config(), report)
    assert result["reason"] == "UNEXPECTED_LOSS"
    assert collector._replay.call_count == failure_at + 1
    assert result["launch_attempts"] == failure_at + 2
    assert result["attempts"][-1]["status"] == "failed"
    assert result["parent_journal_unchanged"]


@pytest.mark.parametrize("failure", ["session", "busy", "source", "lock"])
def test_admission_refuses_before_any_game(harness, monkeypatch, failure):
    report, _ = harness
    if failure == "session":
        collector.load_session.side_effect = ValueError("WINDOWS_SESSION_EXPIRED")
    elif failure == "busy":
        collector.require_idle.side_effect = ValueError("NATIVE_RUNTIME_BUSY")
    elif failure == "source":
        collector.source_identity.side_effect = ValueError("SOURCE_NOT_CLEAN")
    else:
        monkeypatch.setattr(collector.fcntl, "flock", Mock(side_effect=BlockingIOError))
    assert collector.collect(Config(), report)["status"] == "failed"
    collector._capture.assert_not_called()
    collector._replay.assert_not_called()


def test_expiry_between_repetitions_has_no_next_launch(harness):
    report, _ = harness
    # Admission, fixture, first comparison; the next locked preflight expires.
    collector.load_session.side_effect = [None, None, None, ValueError("WINDOWS_SESSION_EXPIRED")]
    result = collector.collect(Config(), report)
    assert result["reason"] == "WINDOWS_SESSION_EXPIRED"
    assert result["launch_attempts"] == 2
    assert result["attempts"][-1]["launch_attempted"] is False
    collector._replay.assert_called_once()
    assert not result["actual_socket_expiry_tested"]


def test_cleanup_error_is_secondary_and_never_turns_failure_into_pass(harness):
    report, _ = harness
    collector._replay.side_effect = NativeFailure("PRIMARY_LOSS")
    collector.require_idle.side_effect = [{}, {}, {}, ValueError("NATIVE_RUNTIME_BUSY")]
    result = collector.collect(Config(), report)
    assert result["reason"] == "PRIMARY_LOSS"
    assert result["attempts"][-1]["cleanup_reason"] == "NATIVE_RUNTIME_BUSY"
    collector._replay.assert_called_once()


def test_close_keeps_original_exception_and_records_secondary():
    row = {}
    close = Mock(side_effect=NativeFailure("CLOSE_FAILED"))
    with pytest.raises(ValueError, match="ORIGINAL"):
        with collector._closing(close, row):
            raise ValueError("ORIGINAL")
    assert row == {"cleanup_reason": "CLOSE_FAILED"}


def test_source_drift_does_not_start_next_game(harness):
    report, _ = harness
    collector.source_identity.side_effect = [{"commit": "test"}, {"commit": "changed"}, {"commit": "changed"}]
    result = collector.collect(Config(), report)
    assert result["reason"] == "INTERRUPTION_SOURCE_OR_ENVIRONMENT_CHANGED"
    assert result["launch_attempts"] == 0
    collector._capture.assert_not_called()


def test_report_never_overwrites_or_follows_mount_or_symlink(harness, tmp_path):
    report, _ = harness
    report.write_text("original")
    link = tmp_path / "mounted"
    link.symlink_to("/mnt/c")
    for target in (report, "/mnt/c/refused.json", link / "refused.json"):
        with pytest.raises(ValueError, match="NEW_LINUX_PATH"):
            collector.collect(Config(), target)
    assert report.read_text() == "original"
    collector._capture.assert_not_called()


def test_replay_retains_actual_divergence_and_closes_owned_game(tmp_path, monkeypatch):
    game = Mock()
    game.bridge.instance_id = "owned"
    game.observe_private.return_value = {"PRIVATE_STATE": True}
    store = Mock()
    store.episode_path.return_value = tmp_path
    fixture = (store, "episode", {"seed": "PRIVATE_SEED", "initial_continuation_hash": "hash"}, [])
    monkeypatch.setattr(collector, "NativeGame", Mock(return_value=game))
    monkeypatch.setattr(collector, "restore_seed_prefix", Mock())
    monkeypatch.setattr(collector, "check_private", Mock(side_effect=ReplayDivergence("PRIVATE_CONTINUATION_DIVERGENCE", game, 0)))
    row = {"scenario": "baseline_1"}
    with pytest.raises(ReplayDivergence):
        collector._replay(Config(), row, fixture)
    artifact = tmp_path / row["divergence_artifact"]
    assert json.loads(artifact.read_text())["actual"] == {"PRIVATE_STATE": True}
    assert "PRIVATE_STATE" not in json.dumps(row)
    game.close.assert_called_once()
    assert row["owned_close_completed"]


@pytest.mark.parametrize("status", ["committed", "unknown"])
def test_transport_uses_one_action_and_never_replays_unknown(monkeypatch, status):
    @contextmanager
    def cut(game, row):
        row.update(action_rpc_sends=1, status_queries=1, request_status=status,
                   cut_after_flush=True, transport_error="WINDOWS_BRIDGE_CLOSED")
        yield
    replay = Mock(side_effect=None if status == "committed" else NativeFailure("ACTION_STATUS_UNKNOWN"))
    monkeypatch.setattr(collector, "cut_action_ack", cut)
    monkeypatch.setattr(collector, "replay_steps", replay)
    game, row = Mock(), {}
    collector._transport(game, "issuer", ["step"], "episode", row)
    replay.assert_called_once_with(game, "issuer", ["step"], "episode")
    assert row["classification"] == ("reconciled_connection_interruption" if status == "committed" else "unknown_game_outcome")
    game.wait_ready.assert_not_called()


def test_hang_requires_real_transport_error_and_confirmed_detach(monkeypatch):
    @contextmanager
    def suspend(game, row):
        row.update(held=True, detached=False)
        yield
        row["detached"] = True
    monkeypatch.setattr(collector, "suspend_owned_game", suspend)
    environment = Config().environment
    game = Mock(bridge=SimpleNamespace(env=environment, _close_rpc=Mock(), rpc=Mock(), verify_identity=Mock()))
    game.wait_ready.side_effect = NativeFailure("WINDOWS_BRIDGE_TIMEOUT")
    row = {}
    collector._hang(game, row)
    assert row["classification"] == "unknown_game_outcome" and row["detached"]
    assert game.bridge.env is environment
    game.wait_ready.side_effect = NativeFailure("NATIVE_PROCESS_IDENTITY_MISMATCH")
    game.bridge._close_rpc.side_effect = [None, NativeFailure("CLOSE_FAILED")]
    row = {}
    with pytest.raises(NativeFailure, match="IDENTITY_MISMATCH"):
        collector._hang(game, row)
    assert game.bridge.env is environment
    assert row["rpc_cleanup_reason"] == "CLOSE_FAILED"


@pytest.mark.parametrize("extra", [[], ["--report", "x", "--continuation-only"], ["--report", "x", "--from-stage", "anything"]])
def test_cli_requires_report_and_exclusive_mode(monkeypatch, extra):
    collect = Mock()
    monkeypatch.setattr(collector, "collect", collect)
    assert main(["evidence", "collect", "--interruption-only", *extra]) == 1
    collect.assert_not_called()


def test_cli_propagates_failed_receipt(monkeypatch):
    monkeypatch.setattr(collector, "collect", Mock(return_value={"status": "failed", "reason": "NATIVE_FAILURE"}))
    assert main(["evidence", "collect", "--interruption-only", "--report", "unused"]) == 1
