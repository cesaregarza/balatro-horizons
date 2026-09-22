"""Bounded unpaid fixture for the cost-boundary probe, not a paid-run resume."""

import fcntl
import subprocess
from pathlib import Path

from balatro_horizons.config import ROOT
from balatro_horizons.evidence.collect.acceptance import Audit
from balatro_horizons.evidence.collect.connection import (
    error_code,
    require_idle,
    require_source_instrumentation,
    source_identity,
)
from balatro_horizons.evidence.continuation_probe import verify_continuation_probe
from balatro_horizons.game.transport import WindowsBridge
from balatro_horizons.game.windows_context import load_session
from balatro_horizons.harness.baselines import candidates
from balatro_horizons.storage.journal import atomic_json, digest, now


def capture_fixture(config, result):
    """Capture only the initial blind boundary; never fabricate cost exhaustion."""
    path = ROOT / "private/native-worker.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as worker:
        try:
            fcntl.flock(worker, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("NATIVE_WORKER_BUSY") from None
        load_session()
        result["idle_before"] = require_idle(config.environment)
        result["fixture_launch_attempted"] = True
        audit = Audit(config, "continuation_probe_boundary")
        try:
            result["episode_id"] = audit.eid
            result["phase"] = audit.obs.phase
            if audit.obs.phase != "BLIND_SELECT":
                raise ValueError("CONTINUATION_FIXTURE_PHASE_MISMATCH")
            action = next(
                item.action.model_dump(mode="json") for item in candidates(audit.obs)
                if item.action.type == "select_blind"
            )
        finally:
            audit.finish("UNPAID_CONTINUATION_PROBE_FIXTURE")
        return audit.store, audit.eid, audit.decision, action


def run_probe(config, result):
    store, eid, decision, action = capture_fixture(config, result)
    result["fixture_closed_before_probe"] = require_idle(config.environment)
    parent_head = store.events(eid)[-1]["hash"]
    result["probe_requested"] = True
    try:
        cert = verify_continuation_probe(store, config, eid, decision, action)
    finally:
        result["parent_journal_unchanged"] = store.events(eid)[-1]["hash"] == parent_head
    # The full action and private state remain in private certificate storage.
    result["certificate"] = {key: cert[key] for key in (
        "certificate_id", "status", "mode", "scope", "checkpoint_hash",
        "implementation_hash", "recorded_implementation_hash", "environment_hash",
        "repetitions", "completed_repetitions", "phase",
    )}
    result["divergence_reasons"] = [item["reason"] for item in cert["failures"]]
    if cert["status"] != "passed":
        raise ValueError("CONTINUATION_FIXTURE_PROBE_FAILED")
    if not result["parent_journal_unchanged"]:
        raise ValueError("CONTINUATION_FIXTURE_PARENT_CHANGED")


def collect(config, report):
    """Four launches at most, stop on failure, no backend or capability activation."""
    report = Path(report).resolve()
    if report.is_relative_to("/mnt") or report.exists():
        raise ValueError("CONTINUATION_REPORT_REQUIRES_NEW_LINUX_PATH")
    result = {
        "schema_version": 1, "scope": "initial_blind_continuation_fixture",
        "created_at": now(), "status": "failed", "maximum_physical_launches": 4,
        "fixture_launch_attempted": False, "probe_requested": False,
        "provider_calls": 0, "cost_usd": 0, "evaluation_eligible": False,
        "cost_stopped_root_verified": False, "paid_continuation_started": False,
        "capability_activation_requested": False,
    }
    try:
        result["source"] = source_identity()
        load_session()
        bridge = WindowsBridge(config.environment)
        bridge.calibration = True
        result["environment_hash"] = digest(bridge.verify_files())
        require_source_instrumentation(bridge)
        try:
            run_probe(config, result)
        finally:
            try:
                result["idle_after"] = require_idle(config.environment)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                result["cleanup_reason"] = error_code(error)
        if "cleanup_reason" in result:
            raise ValueError("CONTINUATION_CLEANUP_NOT_CONFIRMED")
        if source_identity() != result["source"] or digest(bridge.verify_files()) != result["environment_hash"]:
            raise ValueError("CONTINUATION_SOURCE_OR_ENVIRONMENT_CHANGED")
        result["status"] = "passed"
    except (OSError, ValueError, RuntimeError, KeyError, StopIteration, subprocess.SubprocessError) as error:
        result["reason"] = error_code(error)
    atomic_json(report, result, immutable=True)
    report.chmod(0o600)
    return result
