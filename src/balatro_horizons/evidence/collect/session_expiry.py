"""One fresh replay, then real disposable-session expiry before repetition two."""

import fcntl
import hashlib
import json
import os
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path

from balatro_horizons.config import ROOT
from balatro_horizons.evidence.certification import prefix_snapshot, read_checkpoint, steps_for
from balatro_horizons.evidence.collect.connection import (
    error_code,
    require_idle,
    require_source_instrumentation,
    source_identity,
)
from balatro_horizons.evidence.collect.disposable_identity import socket_identity
from balatro_horizons.evidence.collect.disposable_session import disposable_session
from balatro_horizons.evidence.collect.interruption import (
    _check_identity,
    _report_path,
    _retain_divergence,
)
from balatro_horizons.game.replay import (
    ReplayDivergence,
    check_private,
    replay_steps,
    restore_seed_prefix,
)
from balatro_horizons.game.session import NativeGame
from balatro_horizons.game.transport import WindowsBridge
from balatro_horizons.game.windows_context import context_path, load_session, register_session
from balatro_horizons.storage.journal import Store, atomic_json, digest, identifier, now
from balatro_horizons.storage.private_files import atomic_private


def _read_fixture(config, receipt, result):
    path = Path(os.path.abspath(receipt))
    if path.is_relative_to("/mnt") or any(part.is_symlink() for part in (*reversed(path.parents), path)):
        raise ValueError("EXPIRY_FIXTURE_REQUIRES_LINUX_PATH")
    try:
        payload = path.read_bytes()
        prior = json.loads(payload)
    except (OSError, UnicodeError, ValueError):
        raise ValueError("EXPIRY_FIXTURE_UNREADABLE") from None
    if (not isinstance(prior, dict) or not isinstance(prior.get("attempts"), list)
            or not all(isinstance(row, dict) for row in prior["attempts"])
            or not isinstance(prior.get("source"), dict)):
        raise ValueError("EXPIRY_FIXTURE_RECEIPT_MISMATCH")
    expected = ["fixture_capture", "baseline_1", "baseline_2", "baseline_3",
                "divergence", "hang", "transport_loss"]
    if (prior.get("scope") != "native_replay_interruption_classification"
            or prior.get("status") != "passed" or prior.get("launch_attempts") != 7
            or [row.get("scenario") for row in prior.get("attempts", [])] != expected
            or any(row.get("status") != "observed" for row in prior["attempts"])
            or prior.get("parent_journal_unchanged") is not True
            or prior.get("provider_calls") != 0 or prior.get("evaluation_eligible") is not False
            or prior.get("configuration") != {"deck": config.environment.deck, "stake": config.environment.stake}
            or prior.get("environment_hash") != result["environment_hash"]
            or prior.get("source", {}).get("native_implementation_hash") != result["source"]["native_implementation_hash"]):
        raise ValueError("EXPIRY_FIXTURE_RECEIPT_MISMATCH")
    commit = prior["source"].get("commit", "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("EXPIRY_FIXTURE_SOURCE_INVALID")
    # Reusing the unpaid fixture is not recertification. Its collector and replay
    # oracle must remain byte-identical, in addition to the native fingerprint.
    try:
        subprocess.run(["git", "-C", str(ROOT), "diff", "--exit-code", "--quiet", commit, "HEAD", "--",
                        "src/balatro_horizons/evidence/collect/interruption.py",
                        "src/balatro_horizons/evidence/collect/interruption_faults.py",
                        "src/balatro_horizons/evidence/certification.py"], check=True, timeout=10)
    except subprocess.CalledProcessError:
        raise ValueError("EXPIRY_FIXTURE_HEAD_MISMATCH") from None
    store, eid = Store(ROOT / "data"), identifier(prior["episode_id"])
    manifest = store.manifest(eid)
    if (manifest.get("fixture") != "replay_interruption_boundary"
            or manifest.get("evidence_kind") != "NATIVE" or manifest.get("evaluation_eligible") is not False):
        raise ValueError("EXPIRY_REQUIRES_EXCLUDED_NATIVE_FIXTURE")
    checkpoint = read_checkpoint(store, eid, 0)
    if checkpoint.get("implementation_hash") != prior["source"].get("implementation_hash"):
        raise ValueError("EXPIRY_FIXTURE_SOURCE_MISMATCH")
    steps = steps_for(store, eid)
    if len(steps) != 1 or steps[0]["kind"] != "action":
        raise ValueError("EXPIRY_FIXTURE_SUFFIX_MISMATCH")
    snapshot = prefix_snapshot(store, eid, 0, steps)
    if checkpoint.get("continuation_hash") != snapshot.get("initial_continuation_hash"):
        raise ValueError("EXPIRY_FIXTURE_PRIVATE_HASH_MISMATCH")
    result.update(episode_id=eid, fixture_source=prior["source"],
                  fixture_report_sha256=hashlib.sha256(payload).hexdigest())
    return store, eid, snapshot, steps


def _certificates(store, eid):
    paths = [ROOT / "private/capability-certificate.json"]
    private = store.episode_path(eid, True)
    paths.extend(private.glob("certificate*.json"))
    paths.extend(private.glob("continuation-probe*.json"))
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths if path.exists()}


def _replay(config, row, fixture):
    store, eid, snapshot, steps = fixture
    owned = WindowsBridge(config.environment)
    owned.calibration = True
    primary = None
    try:
        owned.launch()
        # Retain the bridge even if construction fails, so restored operator
        # registration can clean up this exact nonce after unexpected expiry.
        game = NativeGame(config.environment, snapshot["seed"], calibration=True, launch=False, bridge=owned)
        issuer = restore_seed_prefix(game, snapshot)
        check_private(game, snapshot["initial_continuation_hash"], 0)
        row["starting_state_matches"] = True
        replay_steps(game, issuer, steps, eid)
        row["settled_state_matches"] = True
    except BaseException as error:
        primary = error
        if isinstance(error, ReplayDivergence):
            row["divergence_artifact"] = _retain_divergence(store, eid, error)
        raise
    finally:
        row["owned_instance_id"] = owned.instance_id
        try:
            if owned.instance_id:
                owned.stop()
            else:
                owned._close_rpc()
            row["owned_close_completed"] = True
        except (OSError, ValueError, RuntimeError) as error:
            row["cleanup_reason"] = error_code(error)
            if primary is None:
                raise


def _recover_owned(config, result):
    for row in result["attempts"]:
        if row.get("owned_close_completed") or not row.get("owned_instance_id"):
            continue
        # Cleanup only, never a game-action resend or replay retry. The stop
        # command verifies this nonce plus executable/PID/start time itself.
        owned = WindowsBridge(config.environment)
        owned.calibration = True
        owned.instance_id = row["owned_instance_id"]
        owned.stop()
        row["owned_cleanup_via_operator_session"] = True


@contextmanager
def _registration(environment, result):
    path = context_path()
    original = path.read_bytes()
    operator = load_session()
    identity = socket_identity(operator)
    primary = None
    try:
        register_session(environment)
        yield
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            atomic_private(path, original)
            result["registration_restored"] = path.read_bytes() == original and load_session() == operator
            result["operator_socket_unchanged"] = socket_identity(operator) == identity
            if not result["registration_restored"] or not result["operator_socket_unchanged"]:
                raise ValueError("EXPIRY_REGISTRATION_RESTORE_MISMATCH")
        except (ValueError, OSError) as error:
            result["registration_cleanup_reason"] = error_code(error)
            if primary is None:
                raise


def _repetitions(config, bridge, fixture, child, result):
    # The same preflight/replay/close order as the interruption collector. Never
    # call public certificate writers or substitute a synthetic session error.
    for repetition in range(1, 4):
        row = {"scenario": "session_expiry", "repetition": repetition,
               "launch_attempted": False, "status": "failed"}
        result["attempts"].append(row)
        _check_identity(bridge, result)
        try:
            load_session()
        except ValueError as error:
            row["reason"] = error_code(error)
            if repetition != 2 or str(error) != "WINDOWS_SESSION_EXPIRED":
                raise
            row["status"] = "refused_before_launch"
            result["classification"] = "connection_only_interruption_before_repetition"
            result["actual_socket_expiry_tested"] = True
            return
        if repetition != 1:
            raise ValueError("EXPIRY_NEXT_REPETITION_NOT_REFUSED")
        require_idle(config.environment)
        row["launch_attempted"] = True
        result["launch_attempts"] += 1
        _replay(config, row, fixture)
        row["idle_after"] = require_idle(config.environment)
        _check_identity(bridge, result)
        if not row.get("owned_close_completed") or not row.get("settled_state_matches"):
            raise ValueError("EXPIRY_FIRST_REPETITION_INCOMPLETE")
        row["status"] = "observed"
        # End the disposable command only after the owned game has closed.
        child.expire()
    raise ValueError("EXPIRY_NOT_OBSERVED")


def _run(config, bridge, fixture, result):
    store, eid, _, _ = fixture
    journal = store.events(eid)[-1]["hash"]
    certificates = _certificates(store, eid)
    primary = None
    try:
        result["idle_before"] = require_idle(config.environment)
        result["disposable_session"] = {}
        with disposable_session(load_session(), result["disposable_session"]) as child:
            with _registration(child.environment, result):
                _repetitions(config, bridge, fixture, child, result)
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            _recover_owned(config, result)
            result["parent_journal_unchanged"] = store.events(eid)[-1]["hash"] == journal
            result["certificates_unchanged"] = _certificates(store, eid) == certificates
            result["idle_after"] = require_idle(config.environment)
            _check_identity(bridge, result)
            if not result["parent_journal_unchanged"] or not result["certificates_unchanged"]:
                raise ValueError("EXPIRY_PARENT_EVIDENCE_CHANGED")
        except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
            result["cleanup_reason"] = error_code(error)
            if primary is None:
                raise


def collect(config, report, fixture_report):
    """One additional game launch at most; no suite retry or certificate writes."""
    report = _report_path(report)
    result = {
        "schema_version": 1, "scope": "native_session_expiry_between_repetitions",
        "created_at": now(), "status": "failed", "maximum_physical_launches": 1,
        "launch_attempts": 0, "attempts": [], "provider_calls": 0, "cost_usd": 0,
        "evaluation_eligible": False, "certificate_selection_changed": False,
        "capability_certificates_created": False, "restoration_certification_included": False,
        "actual_socket_expiry_tested": False, "complete_issue42_acceptance": False,
    }
    try:
        result["source"] = source_identity()
        load_session()
        bridge = WindowsBridge(config.environment)
        bridge.calibration = True
        result["environment_hash"] = digest(bridge.verify_files())
        require_source_instrumentation(bridge)
        with (ROOT / "private/native-worker.lock").open("a") as worker:
            try:
                fcntl.flock(worker, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("NATIVE_WORKER_BUSY") from None
            fixture = _read_fixture(config, fixture_report, result)
            _run(config, bridge, fixture, result)
        if not result["disposable_session"].get("actual_socket_expired"):
            raise ValueError("EXPIRY_SOCKET_DISAPPEARANCE_NOT_CONFIRMED")
        result.update(status="passed", complete_issue42_acceptance=True)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as error:
        result["reason"] = error_code(error)
    finally:
        atomic_json(report, result, immutable=True)
        report.chmod(0o600)
    return result
