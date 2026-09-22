"""Collector wiring doubles: these tests never establish native fidelity."""

import json
from unittest.mock import Mock

import pytest

from balatro_horizons.cli import main
from balatro_horizons.config import Config
from balatro_horizons.evidence.collect import continuation as collector
from balatro_horizons.evidence.stages import plan
from balatro_horizons.game.contract import NativeFailure


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setattr(collector, "ROOT", tmp_path)
    monkeypatch.setattr(collector, "load_session", Mock())
    source = Mock(return_value={"commit": "test", "implementation_hash": "source"})
    monkeypatch.setattr(collector, "source_identity", source)
    bridge = Mock()
    bridge.verify_files.return_value = {"manifest": "pinned"}
    monkeypatch.setattr(collector, "WindowsBridge", Mock(return_value=bridge))
    monkeypatch.setattr(collector, "require_source_instrumentation", Mock())
    idle = Mock(return_value={"owned_processes": 0, "port_listening": False})
    monkeypatch.setattr(collector, "require_idle", idle)
    audit = Mock(eid="fixture", decision=0)
    audit.obs.phase = "BLIND_SELECT"
    audit.store.events.return_value = [{"hash": "parent"}]
    monkeypatch.setattr(collector, "Audit", Mock(return_value=audit))
    action = Mock(type="select_blind")
    action.model_dump.return_value = {"type": "select_blind", "blind_id": "private-handle"}
    monkeypatch.setattr(collector, "candidates", lambda obs: [Mock(action=action)])
    cert = {
        "certificate_id": "cert", "status": "passed", "mode": "checkpoint_probe",
        "scope": "original_state_and_generated_same_action_probe", "checkpoint_hash": "checkpoint",
        "implementation_hash": "source", "recorded_implementation_hash": "source",
        "environment_hash": "environment", "repetitions": 3, "completed_repetitions": 3,
        "phase": "BLIND_SELECT", "failures": [], "private": "never print",
    }
    probe = Mock(return_value=cert)
    monkeypatch.setattr(collector, "verify_continuation_probe", probe)
    return audit, probe, idle, source, tmp_path / "receipt.json"


def test_four_launch_plan_executes_nothing(capsys):
    assert main(["evidence", "plan", "--continuation-only"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["expected_physical_launches"] == result["expected_game_resets"] == 4
    assert not result["release_certification_requested"]
    assert not result["native_evidence_collected"]
    with pytest.raises(ValueError, match="MUTUALLY_EXCLUSIVE"):
        plan(continuation_only=True, connection_only=True)


def test_fixture_closes_before_probe_and_receipt_excludes_private_data(harness):
    audit, probe, _, _, report = harness
    def verify(*args):
        audit.finish.assert_called_once_with("UNPAID_CONTINUATION_PROBE_FIXTURE")
        assert args[2:] == ("fixture", 0, {"type": "select_blind", "blind_id": "private-handle"})
        return probe.return_value
    probe.side_effect = verify
    result = collector.collect(Config(), report)
    assert result["status"] == "passed" and result["parent_journal_unchanged"]
    assert result["certificate"]["completed_repetitions"] == 3
    assert result["provider_calls"] == result["cost_usd"] == 0
    assert not result["cost_stopped_root_verified"] and not result["paid_continuation_started"]
    assert not result["capability_activation_requested"]
    assert json.loads(report.read_text()) == result
    assert report.stat().st_mode & 0o777 == 0o600
    assert "private-handle" not in report.read_text() and "never print" not in report.read_text()


@pytest.mark.parametrize("code", ["NATIVE_RUNTIME_BUSY", "WINDOWS_SESSION_EXPIRED"])
def test_preflight_failure_prevents_fixture_and_probe(harness, monkeypatch, code):
    _, probe, idle, _, report = harness
    if code == "NATIVE_RUNTIME_BUSY":
        idle.side_effect = ValueError(code)
    else:
        collector.load_session.side_effect = ValueError(code)
    result = collector.collect(Config(), report)
    assert result["status"] == "failed" and result["reason"] == code
    collector.Audit.assert_not_called()
    probe.assert_not_called()


def test_operational_probe_failure_is_preserved_without_retry(harness):
    audit, probe, idle, _, report = harness
    probe.side_effect = NativeFailure("NATIVE_SETTLING_TIMEOUT")
    result = collector.collect(Config(), report)
    assert result["status"] == "failed" and result["reason"] == "NATIVE_SETTLING_TIMEOUT"
    assert result["parent_journal_unchanged"] and result["idle_after"]["owned_processes"] == 0
    assert "certificate" not in result
    probe.assert_called_once()
    audit.finish.assert_called_once()
    assert idle.call_count == 3


def test_cleanup_failure_does_not_erase_primary_error(harness):
    _, probe, idle, _, report = harness
    probe.side_effect = NativeFailure("NATIVE_SETTLING_TIMEOUT")
    idle.side_effect = [{}, {}, ValueError("NATIVE_RUNTIME_BUSY")]
    result = collector.collect(Config(), report)
    assert result["reason"] == "NATIVE_SETTLING_TIMEOUT"
    assert result["cleanup_reason"] == "NATIVE_RUNTIME_BUSY"


def test_fixture_closes_when_action_selection_fails(harness, monkeypatch):
    audit, probe, _, _, report = harness
    monkeypatch.setattr(collector, "candidates", lambda obs: [])
    assert collector.collect(Config(), report)["status"] == "failed"
    audit.finish.assert_called_once()
    probe.assert_not_called()


def test_busy_worker_never_creates_fixture(harness, monkeypatch):
    _, probe, _, _, report = harness
    monkeypatch.setattr(collector.fcntl, "flock", Mock(side_effect=BlockingIOError))
    assert collector.collect(Config(), report)["reason"] == "NATIVE_WORKER_BUSY"
    collector.Audit.assert_not_called()
    probe.assert_not_called()


def test_real_journal_checkpoint_and_probe_wiring_with_synthetic_game(tmp_path, monkeypatch):
    from balatro_horizons.evidence.collect import acceptance
    from balatro_horizons.game.fake import FakeGame

    monkeypatch.setattr(acceptance, "ROOT", tmp_path)
    monkeypatch.setattr(collector, "ROOT", tmp_path)
    monkeypatch.setattr(collector, "source_identity", lambda: {"commit": "test"})
    monkeypatch.setattr(collector, "load_session", Mock())
    monkeypatch.setattr(collector, "require_idle", lambda env: {})
    monkeypatch.setattr(collector, "require_source_instrumentation", Mock())
    bridge = Mock()
    bridge.verify_files.return_value = {}
    monkeypatch.setattr(collector, "WindowsBridge", Mock(return_value=bridge))
    def audit(config, case):
        return acceptance.Audit(config, case, game_factory=lambda env, seed: FakeGame(seed))
    monkeypatch.setattr(collector, "Audit", audit)
    result = collector.collect(Config(), tmp_path / "receipt.json")
    assert result["status"] == "passed"
    assert result["parent_journal_unchanged"]
    assert result["certificate"]["completed_repetitions"] == 3


def test_divergence_keeps_certificate_summary_and_never_retries(harness):
    _, probe, _, _, report = harness
    probe.return_value.update(status="failed", completed_repetitions=0,
                              failures=[{"reason": "PRIVATE_CONTINUATION_DIVERGENCE", "actual": "private"}])
    result = collector.collect(Config(), report)
    assert result["status"] == "failed"
    assert result["divergence_reasons"] == ["PRIVATE_CONTINUATION_DIVERGENCE"]
    assert result["certificate"]["status"] == "failed"
    probe.assert_called_once()
    assert "actual" not in report.read_text()


def test_source_drift_and_existing_receipt_are_refused(harness):
    _, probe, _, source, report = harness
    source.side_effect = [{"commit": "before"}, {"commit": "after"}]
    result = collector.collect(Config(), report)
    assert result["reason"] == "CONTINUATION_SOURCE_OR_ENVIRONMENT_CHANGED"
    before = report.read_bytes()
    with pytest.raises(ValueError, match="NEW_LINUX_PATH"):
        collector.collect(Config(), report)
    assert report.read_bytes() == before
    probe.assert_called_once()


@pytest.mark.parametrize("extra", [[], ["--report", "x", "--gameplay-only"], ["--report", "x", "--episode-id", "x"]])
def test_cli_rejects_missing_report_or_conflicting_modes(monkeypatch, extra):
    collect = Mock()
    monkeypatch.setattr(collector, "collect", collect)
    assert main(["evidence", "collect", "--continuation-only", *extra]) == 1
    collect.assert_not_called()


def test_cli_failure_is_nonzero(monkeypatch):
    monkeypatch.setattr(collector, "collect", Mock(return_value={"status": "failed", "reason": "PROBE_FAILED"}))
    assert main(["evidence", "collect", "--continuation-only", "--report", "x"]) == 1
