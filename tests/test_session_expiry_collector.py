"""Offline ordering and receipt guards; not native socket-expiry evidence."""

import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from balatro_horizons.cli import main
from balatro_horizons.config import Config
from balatro_horizons.evidence.collect import session_expiry as collector
from balatro_horizons.evidence.stages import plan
from balatro_horizons.storage.private_files import atomic_private


def test_expiry_plan_never_requests_certification(capsys):
    assert main(["evidence", "plan", "--session-expiry-only"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["expected_physical_launches"] == 1
    assert not result["release_certification_requested"]
    for mode in ("interruption_only", "continuation_only", "connection_only", "gameplay_only",
                 "resume_actions", "resume_certification"):
        with pytest.raises(ValueError, match="MUTUALLY_EXCLUSIVE"):
            plan(session_expiry_only=True, **{mode: True})


def test_real_preflight_boundary_follows_close_then_expiry(monkeypatch):
    order, result = [], {"attempts": [], "launch_attempts": 0}
    monkeypatch.setattr(collector, "_check_identity", lambda *args: None)
    monkeypatch.setattr(collector, "require_idle", lambda *args: {})
    session = Mock(side_effect=[{}, ValueError("WINDOWS_SESSION_EXPIRED")])
    monkeypatch.setattr(collector, "load_session", session)

    def replay(config, row, fixture):
        order.append("closed")
        row.update(owned_close_completed=True, settled_state_matches=True)

    monkeypatch.setattr(collector, "_replay", replay)
    child = SimpleNamespace(expire=lambda: order.append("expired"))
    collector._repetitions(Config(), None, None, child, result)
    assert order == ["closed", "expired"]
    assert result["launch_attempts"] == 1
    assert result["actual_socket_expiry_tested"]
    assert result["attempts"][1]["status"] == "refused_before_launch"
    assert not result["attempts"][1]["launch_attempted"]
    assert session.call_count == 2


@pytest.mark.parametrize("error", [None, "WINDOWS_SESSION_REGISTRATION_INVALID"])
def test_nonexpiry_second_preflight_never_launches(monkeypatch, error):
    monkeypatch.setattr(collector, "_check_identity", Mock())
    monkeypatch.setattr(collector, "require_idle", Mock())
    monkeypatch.setattr(collector, "load_session", Mock(side_effect=[{}, ValueError(error) if error else {}]))
    replay = Mock(side_effect=lambda config, row, fixture: row.update(owned_close_completed=True, settled_state_matches=True))
    monkeypatch.setattr(collector, "_replay", replay)
    result = {"attempts": [], "launch_attempts": 0}
    with pytest.raises(ValueError):
        collector._repetitions(Config(), None, None, Mock(), result)
    assert result["launch_attempts"] == 1
    replay.assert_called_once()


def test_first_replay_failure_does_not_request_expiry(monkeypatch):
    monkeypatch.setattr(collector, "_check_identity", Mock())
    monkeypatch.setattr(collector, "require_idle", Mock())
    monkeypatch.setattr(collector, "load_session", Mock())
    monkeypatch.setattr(collector, "_replay", Mock(side_effect=ValueError("DIVERGENCE")))
    child = Mock()
    with pytest.raises(ValueError, match="DIVERGENCE"):
        collector._repetitions(Config(), None, None, child, {"attempts": [], "launch_attempts": 0})
    child.expire.assert_not_called()


def test_registration_restores_exact_bytes_even_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "session.json"
    original = b'{"private": "original bytes"}\n'
    atomic_private(path, original)
    monkeypatch.setattr(collector, "context_path", lambda: path)
    monkeypatch.setattr(collector, "load_session", lambda: {"original": True})
    monkeypatch.setattr(collector, "socket_identity", lambda env: {"inode": 42})
    monkeypatch.setattr(collector, "register_session", lambda env: atomic_private(path, b"temporary"))
    result = {}
    with pytest.raises(ValueError, match="PRIMARY"):
        with collector._registration({}, result):
            assert path.read_bytes() == b"temporary"
            raise ValueError("PRIMARY")
    assert path.read_bytes() == original
    assert result == {"registration_restored": True, "operator_socket_unchanged": True}
    assert path.stat().st_mode & 0o777 == 0o600


def test_restore_failure_preserves_primary(tmp_path, monkeypatch):
    path = tmp_path / "session.json"
    path.write_bytes(b"original")
    monkeypatch.setattr(collector, "context_path", lambda: path)
    monkeypatch.setattr(collector, "load_session", Mock())
    monkeypatch.setattr(collector, "socket_identity", Mock())
    monkeypatch.setattr(collector, "register_session", Mock())
    monkeypatch.setattr(collector, "atomic_private", Mock(side_effect=ValueError("RESTORE_FAILED")))
    result = {}
    with pytest.raises(ValueError, match="PRIMARY"):
        with collector._registration({}, result):
            raise ValueError("PRIMARY")
    assert result["registration_cleanup_reason"] == "RESTORE_FAILED"


@pytest.fixture
def harness(tmp_path, monkeypatch):
    (tmp_path / "private").mkdir()
    monkeypatch.setattr(collector, "ROOT", tmp_path)
    monkeypatch.setattr(collector, "source_identity", Mock(return_value={"commit": "source"}))
    monkeypatch.setattr(collector, "load_session", Mock())
    monkeypatch.setattr(collector, "WindowsBridge", Mock())
    collector.WindowsBridge.return_value.verify_files.return_value = {}
    monkeypatch.setattr(collector, "require_source_instrumentation", Mock())
    monkeypatch.setattr(collector, "_read_fixture", Mock())
    monkeypatch.setattr(collector, "_run", Mock())
    return tmp_path / "report.json"


def test_missing_expiry_cannot_pass_and_receipt_is_immutable(harness):
    collector._run.side_effect = lambda config, bridge, fixture, result: result.update(disposable_session={})
    result = collector.collect(Config(), harness, "prior.json")
    assert result["status"] == "failed"
    assert result["reason"] == "EXPIRY_SOCKET_DISAPPEARANCE_NOT_CONFIRMED"
    assert not result["complete_issue42_acceptance"]
    assert json.loads(harness.read_text()) == result
    assert harness.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="NEW_LINUX_PATH"):
        collector.collect(Config(), harness, "prior.json")


def test_busy_lock_refuses_before_disposable_child(harness, monkeypatch):
    monkeypatch.setattr(collector.fcntl, "flock", Mock(side_effect=BlockingIOError))
    result = collector.collect(Config(), harness, "prior.json")
    assert result["reason"] == "NATIVE_WORKER_BUSY"
    collector._run.assert_not_called()


def test_parent_guards_and_primary_cleanup_error(monkeypatch):
    store = Mock()
    store.events.side_effect = [[{"hash": "before"}], [{"hash": "after"}]]
    monkeypatch.setattr(collector, "_certificates", Mock(return_value={}))
    monkeypatch.setattr(collector, "require_idle", Mock())
    monkeypatch.setattr(collector, "_check_identity", Mock())
    monkeypatch.setattr(collector, "load_session", Mock())

    @contextmanager
    def child(*args):
        raise ValueError("CHILD_FAILED")
        yield

    monkeypatch.setattr(collector, "disposable_session", child)
    result = {"attempts": []}
    with pytest.raises(ValueError, match="CHILD_FAILED"):
        collector._run(Config(), None, (store, "eid", {}, []), result)
    assert not result["parent_journal_unchanged"]
    assert result["cleanup_reason"] == "EXPIRY_PARENT_EVIDENCE_CHANGED"


def test_failed_start_retains_nonce_for_owned_cleanup_after_restore(monkeypatch):
    bridge = Mock(instance_id="a" * 32)
    bridge.launch.side_effect = ValueError("FIRST_FAILURE")
    bridge.stop.side_effect = ValueError("WINDOWS_SESSION_EXPIRED")
    monkeypatch.setattr(collector, "WindowsBridge", Mock(return_value=bridge))
    row = {}
    with pytest.raises(ValueError, match="FIRST_FAILURE"):
        collector._replay(Config(), row, (Mock(), "eid", {"seed": "PRIVATE"}, []))
    assert row["owned_instance_id"] == "a" * 32
    assert row["cleanup_reason"] == "WINDOWS_SESSION_EXPIRED"
    recovery = Mock()
    collector.WindowsBridge.return_value = recovery
    collector._recover_owned(Config(), {"attempts": [row]})
    assert recovery.instance_id == "a" * 32
    recovery.stop.assert_called_once()
    recovery.launch.assert_not_called()
    assert row["owned_cleanup_via_operator_session"]


def test_successful_close_never_triggers_recovery(monkeypatch):
    create = Mock()
    monkeypatch.setattr(collector, "WindowsBridge", create)
    collector._recover_owned(Config(), {"attempts": [{"owned_close_completed": True, "owned_instance_id": "owned"}, {}]})
    create.assert_not_called()


@pytest.mark.parametrize("args", [[], ["--report", "new.json"],
                                  ["--report", "new.json", "--fixture-report", "prior.json", "--interruption-only"]])
def test_cli_requires_explicit_fixture_and_new_receipt(args, capsys):
    assert main(["evidence", "collect", "--session-expiry-only", *args]) != 0
    assert json.loads(capsys.readouterr().err)["error"] == "SESSION_EXPIRY_REQUIRES_TWO_REPORTS_AND_NO_OTHER_MODE"


@pytest.fixture
def prior_fixture(tmp_path, monkeypatch):
    prior = {
        "scope": "native_replay_interruption_classification", "status": "passed",
        "launch_attempts": 7, "parent_journal_unchanged": True,
        "provider_calls": 0, "evaluation_eligible": False, "episode_id": "a" * 32,
        "configuration": {"deck": Config().environment.deck, "stake": Config().environment.stake}, "environment_hash": "runtime",
        "source": {"commit": "b" * 40, "native_implementation_hash": "native", "implementation_hash": "original"},
        "attempts": [{"scenario": name, "status": "observed"} for name in
                     ("fixture_capture", "baseline_1", "baseline_2", "baseline_3", "divergence", "hang", "transport_loss")],
    }
    store = Mock()
    store.manifest.return_value = {"fixture": "replay_interruption_boundary", "evidence_kind": "NATIVE", "evaluation_eligible": False}
    monkeypatch.setattr(collector, "Store", Mock(return_value=store))
    monkeypatch.setattr(collector, "read_checkpoint", Mock(return_value={"implementation_hash": "original", "continuation_hash": "private-hash"}))
    monkeypatch.setattr(collector, "steps_for", Mock(return_value=[{"kind": "action"}]))
    monkeypatch.setattr(collector, "prefix_snapshot", Mock(return_value={"seed": "PRIVATE", "initial_continuation_hash": "private-hash"}))
    monkeypatch.setattr(collector.subprocess, "run", Mock())
    return tmp_path / "prior.json", prior, store


def test_reuse_retains_original_source_and_never_exports_seed(prior_fixture):
    path, prior, store = prior_fixture
    path.write_text(json.dumps(prior))
    result = {"environment_hash": "runtime", "source": {"native_implementation_hash": "native"}}
    fixture = collector._read_fixture(Config(), path, result)
    assert fixture[0] is store
    assert result["fixture_source"] == prior["source"]
    assert len(result["fixture_report_sha256"]) == 64
    assert "PRIVATE" not in json.dumps(result)
    collector.subprocess.run.assert_called_once()
    assert "--quiet" in collector.subprocess.run.call_args.args[0]
    assert prior["source"]["commit"] in collector.subprocess.run.call_args.args[0]


@pytest.mark.parametrize("field,value", [("status", "failed"), ("launch_attempts", 6),
                                         ("provider_calls", 1), ("evaluation_eligible", True),
                                         ("environment_hash", "other"), ("attempts", []),
                                         ("configuration", {"deck": "RED", "stake": "DIFFERENT"})])
def test_refuses_incompatible_prior_receipt(prior_fixture, field, value):
    path, prior, _ = prior_fixture
    prior[field] = value
    path.write_text(json.dumps(prior))
    with pytest.raises(ValueError, match="EXPIRY_FIXTURE_RECEIPT_MISMATCH"):
        collector._read_fixture(Config(), path, {"environment_hash": "runtime", "source": {"native_implementation_hash": "native"}})
    collector.subprocess.run.assert_not_called()


def test_fixture_checkpoint_keeps_historical_implementation_hash(prior_fixture):
    path, prior, _ = prior_fixture
    path.write_text(json.dumps(prior))
    collector.read_checkpoint.return_value = {"implementation_hash": "changed"}
    with pytest.raises(ValueError, match="EXPIRY_FIXTURE_SOURCE_MISMATCH"):
        collector._read_fixture(Config(), path, {"environment_hash": "runtime", "source": {"native_implementation_hash": "native"}})


def test_input_symlinks_and_mounted_paths_refused_without_read(tmp_path):
    link = tmp_path / "mounted"
    link.symlink_to("/mnt/c")
    for path in ("/mnt/c/prior.json", link / "prior.json"):
        with pytest.raises(ValueError, match="REQUIRES_LINUX_PATH"):
            collector._read_fixture(Config(), path, {})


@pytest.mark.parametrize("prior", [None, [], {"attempts": [None]}, {"attempts": [], "source": []}])
def test_malformed_prior_receipt_is_sanitized(tmp_path, prior):
    path = tmp_path / "prior.json"
    path.write_text(json.dumps(prior))
    with pytest.raises(ValueError, match="EXPIRY_FIXTURE_RECEIPT_MISMATCH"):
        collector._read_fixture(Config(), path, {})


def test_changed_private_hash_cannot_be_used_as_replay_oracle(prior_fixture):
    path, prior, _ = prior_fixture
    path.write_text(json.dumps(prior))
    collector.prefix_snapshot.return_value = {"initial_continuation_hash": "tampered"}
    with pytest.raises(ValueError, match="EXPIRY_FIXTURE_PRIVATE_HASH_MISMATCH"):
        collector._read_fixture(Config(), path, {"environment_hash": "runtime", "source": {"native_implementation_hash": "native"}})
