"""Bounded native fault classification; never writes or selects certificates."""

import fcntl
import os
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path

from balatro_horizons.config import ROOT
from balatro_horizons.evidence.certification import prefix_snapshot, steps_for
from balatro_horizons.evidence.collect.acceptance import Audit
from balatro_horizons.evidence.collect.connection import (
    error_code,
    require_idle,
    require_source_instrumentation,
    source_identity,
)
from balatro_horizons.evidence.collect.interruption_faults import (
    cut_action_ack,
    suspend_owned_game,
)
from balatro_horizons.game.contract import NativeFailure
from balatro_horizons.game.replay import (
    ReplayDivergence,
    check_private,
    replay_steps,
    restore_seed_prefix,
)
from balatro_horizons.game.session import NativeGame
from balatro_horizons.game.transport import WindowsBridge
from balatro_horizons.game.windows_context import load_session
from balatro_horizons.harness.baselines import candidates
from balatro_horizons.storage.journal import atomic_json, digest, now


def _report_path(report):
    path = Path(os.path.abspath(report))
    if path.is_relative_to("/mnt"):
        raise ValueError("INTERRUPTION_REPORT_REQUIRES_NEW_LINUX_PATH")
    for part in (*reversed(path.parents), path):
        if part.is_symlink():
            raise ValueError("INTERRUPTION_REPORT_REQUIRES_NEW_LINUX_PATH")
    if path.exists():
        raise ValueError("INTERRUPTION_REPORT_REQUIRES_NEW_LINUX_PATH")
    return path


def _check_identity(bridge, result):
    if source_identity() != result["source"] or digest(bridge.verify_files()) != result["environment_hash"]:
        raise ValueError("INTERRUPTION_SOURCE_OR_ENVIRONMENT_CHANGED")
    require_source_instrumentation(bridge)


@contextmanager
def _attempt(config, bridge, result, name):
    row = {"scenario": name, "status": "failed", "launch_attempted": False, "started_at": now()}
    result["attempts"].append(row)
    primary = None
    try:
        _check_identity(bridge, result)
        load_session()
        require_idle(config.environment)
        row["launch_attempted"] = True
        result["launch_attempts"] += 1
        yield row
        row["status"] = "observed"
    except BaseException as error:
        primary = error
        row["reason"] = error_code(error)
        raise
    finally:
        row["finished_at"] = now()
        try:
            row["idle_after"] = require_idle(config.environment)
            _check_identity(bridge, result)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            row["status"] = "failed"
            row["cleanup_reason"] = error_code(error)
            if primary is None:
                raise


@contextmanager
def _closing(close, row):
    primary = None
    try:
        yield
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            close()
            row["owned_close_completed"] = True
        except BaseException as error:
            row["cleanup_reason"] = error_code(error)
            if primary is None:
                raise


def _capture(config, row):
    audit = Audit(config, "replay_interruption_boundary")
    row["episode_id"] = audit.eid
    row["owned_instance_id"] = audit.game.bridge.instance_id
    with _closing(lambda: audit.finish("UNPAID_INTERRUPTION_FIXTURE"), row):
        if audit.obs.phase != "BLIND_SELECT":
            raise ValueError("INTERRUPTION_FIXTURE_PHASE_MISMATCH")
        action = next(item.action.model_dump(mode="json") for item in candidates(audit.obs)
                      if item.action.type == "select_blind")
        audit.take(action)
    steps = steps_for(audit.store, audit.eid)
    if len(steps) != 1 or steps[0]["kind"] != "action":
        raise ValueError("INTERRUPTION_FIXTURE_SUFFIX_MISMATCH")
    snapshot = prefix_snapshot(audit.store, audit.eid, 0, steps)
    return audit.store, audit.eid, snapshot, steps


def _retain_divergence(store, eid, error):
    name = "interruption-divergence-" + uuid.uuid4().hex + ".json"
    path = store.episode_path(eid, True) / name
    atomic_json(path, {"actual": error.actual, "decision": error.decision}, immutable=True)
    path.chmod(0o600)
    return name


def _divergence(game, row, snapshot):
    game.fixture("reorder_inventory")
    game.wait_ready()
    try:
        check_private(game, snapshot["initial_continuation_hash"], 0)
    except ReplayDivergence:
        row["classification"] = "proven_state_divergence"
        row["deliberate_native_state_perturbation"] = True
        raise
    raise ValueError("INTERRUPTION_DIVERGENCE_NOT_OBSERVED")


def _hang(game, row):
    bridge = game.bridge
    original = bridge.env
    bridge._close_rpc()
    bridge.env = original.model_copy(update={"http_timeout_seconds": 2, "rpc_response_margin_seconds": 2})
    try:
        bridge.verify_identity(bridge.rpc("bh_inspect"))
        with suspend_owned_game(game, row):
            try:
                game.wait_ready()
            except NativeFailure as error:
                if str(error) not in {"WINDOWS_BRIDGE_TIMEOUT", "WINDOWS_BRIDGE_CLOSED", "RPC_TRANSPORT_UNKNOWN"}:
                    raise
                row["operation_error"] = error_code(error)
            else:
                raise ValueError("INTERRUPTION_HANG_NOT_OBSERVED")
        if not row.get("held") or not row.get("detached"):
            raise ValueError("INTERRUPTION_HANG_CLEANUP_NOT_CONFIRMED")
        row["classification"] = "unknown_game_outcome"
    finally:
        try:
            bridge._close_rpc()
        finally:
            bridge.env = original


def _transport(game, issuer, steps, eid, row):
    with cut_action_ack(game, row):
        try:
            replay_steps(game, issuer, steps, eid)
        except NativeFailure as error:
            row["operation_error"] = error_code(error)
            row["classification"] = "unknown_game_outcome"
        else:
            if row.get("request_status") != "committed":
                raise ValueError("INTERRUPTION_COMMIT_NOT_CONFIRMED")
            row["classification"] = "reconciled_connection_interruption"
            row["settled_state_matches"] = True
    if row.get("action_rpc_sends") != 1 or row.get("status_queries") != 1 or not row.get("cut_after_flush"):
        raise ValueError("INTERRUPTION_TRANSPORT_INJECTION_NOT_CONFIRMED")
    if not row.get("transport_error"):
        raise ValueError("INTERRUPTION_TRANSPORT_ERROR_NOT_OBSERVED")


def _replay(config, row, fixture):
    store, eid, snapshot, steps = fixture
    game = NativeGame(config.environment, snapshot["seed"], calibration=True)
    row["owned_instance_id"] = game.bridge.instance_id
    with _closing(game.close, row):
        try:
            issuer = restore_seed_prefix(game, snapshot)
            check_private(game, snapshot["initial_continuation_hash"], 0)
            row["starting_state_matches"] = True
            if row["scenario"] == "divergence":
                _divergence(game, row, snapshot)
            elif row["scenario"] == "hang":
                _hang(game, row)
            elif row["scenario"] == "transport_loss":
                _transport(game, issuer, steps, eid, row)
            else:
                replay_steps(game, issuer, steps, eid)
                row["settled_state_matches"] = True
        except ReplayDivergence as error:
            row["divergence_artifact"] = _retain_divergence(store, eid, error)
            row["divergence_reason"] = error_code(error)
            if not row.get("deliberate_native_state_perturbation"):
                raise


def _run(config, bridge, result):
    with _attempt(config, bridge, result, "fixture_capture") as row:
        fixture = _capture(config, row)
    store, eid, _, _ = fixture
    result["episode_id"] = eid
    head = store.events(eid)[-1]["hash"]
    try:
        for name in ("baseline_1", "baseline_2", "baseline_3", "divergence", "hang", "transport_loss"):
            with _attempt(config, bridge, result, name) as row:
                _replay(config, row, fixture)
    finally:
        result["parent_journal_unchanged"] = store.events(eid)[-1]["hash"] == head
    if not result["parent_journal_unchanged"]:
        raise ValueError("INTERRUPTION_PARENT_JOURNAL_CHANGED")


def _admit_and_run(config, result):
    result["source"] = source_identity()
    load_session()
    bridge = WindowsBridge(config.environment)
    bridge.calibration = True
    result["environment_hash"] = digest(bridge.verify_files())
    require_source_instrumentation(bridge)
    path = ROOT / "private/native-worker.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as worker:
        try:
            fcntl.flock(worker, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("NATIVE_WORKER_BUSY") from None
        _run(config, bridge, result)


def collect(config, report):
    """At most seven attempts, stop on unexpected failure, never retry a suite."""
    report = _report_path(report)
    result = {
        "schema_version": 1, "scope": "native_replay_interruption_classification",
        "created_at": now(), "status": "failed", "maximum_physical_launches": 7,
        "launch_attempts": 0, "attempts": [], "provider_calls": 0, "cost_usd": 0,
        "evaluation_eligible": False, "certificate_selection_changed": False,
        "capability_certificates_created": False, "restoration_certification_included": False,
        "actual_socket_expiry_tested": False, "complete_issue42_acceptance": False,
        "configuration": {"deck": config.environment.deck, "stake": config.environment.stake},
    }
    try:
        _admit_and_run(config, result)
        result["status"] = "passed"
    except (OSError, ValueError, RuntimeError, KeyError, StopIteration, subprocess.SubprocessError) as error:
        result["reason"] = error_code(error)
    finally:
        atomic_json(report, result, immutable=True)
        report.chmod(0o600)
    return result
